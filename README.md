# English AI Learning (Flask + Gemini Microservice)

Ứng dụng học tiếng Anh cá nhân hoá với AI (Gemini) gồm:
- Sinh bài tập (grammar/vocabulary) và chấm điểm
- Theo dõi điểm yếu (error rate theo chủ đề/tiểu chủ đề)
- Spaced Repetition (SM-2) cho lịch ôn tập
- Luyện viết (đề viết + chấm bài + gợi ý sửa)
- Flashcards (AI sinh thẻ + ôn theo SM-2)
- Speaking prompts
- Chat Tutor (sửa lỗi nhẹ nhàng + tổng kết phiên)
- Gamification: XP, level, nhiệm vụ ngày, huy hiệu, leaderboard tuần

---

## 1) Công nghệ & kiến trúc

**Kiến trúc microservice**
- `web_app` (Flask): UI + business logic + DB (SQLite)
- `ai_service` (Flask): gọi Gemini qua `google-genai`, trả JSON chuẩn cho web_app

**Stack chính**
- Backend web: Flask, SQLAlchemy, Flask-Login, Flask-Migrate
- DB: SQLite (file trong `instance/`)
- AI: Gemini (qua microservice `ai_service`)
- Frontend: Bootstrap 5 + Jinja templates

---

## 2) Cấu trúc thư mục

- `app/` – Flask app chính
  - `routes/` – các blueprint: auth, main (dashboard/settings), exercises, writing, flashcards, speaking, chat
  - `models.py` – ORM models + seed + helper schema (SQLite ALTER/CREATE khi thiếu cột/bảng)
  - `llm_service.py` – gọi `ai_service`, retry/backoff, cooldown, cache, rate limit, fallback
  - `sm2.py` – thuật toán SM-2
  - `gamification.py` – XP/level/nhiệm vụ/huy hiệu/leaderboard
  - `queue_service.py` – (tuỳ chọn) prefetch bài tập để giảm độ trễ
  - `templates/`, `static/` – giao diện
- `ai_service/` – microservice AI (Gemini)
- `docker-compose.yml`, `Dockerfile` – chạy bằng Docker

---

## 3) Cài đặt & chạy

### Cách A — Chạy bằng Docker Compose (khuyến nghị)

1) Tạo file `.env` ở thư mục gốc:

```env
GEMINI_API_KEY=your_key_here
SECRET_KEY=your_secret_here
```

2) Chạy:

```bash
docker compose up --build
```

- Web app: http://localhost:5000
- AI service: http://localhost:5001

### Cách B — Chạy local (2 terminal)

**Terminal 1 (AI service)**
```bash
cd ai_service
pip install -r requirements.txt
python app.py
```

**Terminal 2 (Web app)**
```bash
pip install -r requirements.txt
python run.py
```

---

## 4) Biến môi trường

### Bắt buộc / khuyến nghị
- `GEMINI_API_KEY`: key Gemini cho `ai_service`
- `SECRET_KEY`: dùng để:
  - Flask session (web_app)
  - Header `X-API-KEY` khi web_app gọi ai_service

### Tuỳ chọn (tối ưu & kiểm soát)
- `AI_SERVICE_URL` (mặc định: `http://ai_service:5001/generate`)
- `ENABLE_EXERCISE_PREFETCH=true|false` (bật prefetch pool)
- `AI_SERVICE_RETRIES` (mặc định 3), `AI_SERVICE_BACKOFF`, `AI_SERVICE_TIMEOUT`
- `AI_SERVICE_COOLDOWN` (mặc định 60s), `AI_SERVICE_MAX_QUOTA_COOLDOWN` (mặc định 300s)
- `AI_RATE_LIMIT_WINDOW_SECONDS`, `AI_RATE_LIMIT_MAX_REQUESTS`

---

## 5) Cơ sở dữ liệu & dữ liệu học

Ứng dụng dùng SQLite qua SQLAlchemy. Các bảng chính:
- `users`: tài khoản, streak, error_threshold, XP/level…
- `topics`: catalog chủ đề (grammar/vocabulary)
- `user_topic_progress`: thống kê + SM-2 (EF, interval, repetitions, next_review_date)
- `user_subtopic_progress`: theo dõi điểm yếu theo `question_type` + `subtopic`
- `exercise_history`: lưu raw JSON bài làm + score
- `writing_prompts`, `writing_submissions`
- `flashcard_sets`, `flashcards` (SM-2 cho thẻ)
- `chat_sessions`, `chat_messages`
- `xp_events`, `daily_missions`, `badges`, `user_badges`

Cơ chế cập nhật học tập:
- Sau khi nộp bài: cập nhật error_rate + SM-2 + subtopic weakness + thưởng XP/huy hiệu/nhiệm vụ
- Dashboard gợi ý nội dung cần ôn dựa trên:
  - `error_threshold` (ngưỡng lỗi trong Settings)
  - quá hạn ôn (`next_review_date`)
  - điểm yếu theo subtopic

---

## 6) AI được dùng như thế nào? (phản ánh việc sử dụng AI)

### 6.1) Luồng gọi AI
Web app gọi AI qua microservice:
- `app/llm_service.py` gửi request JSON đến `ai_service /generate`
- `ai_service/app.py` dùng Gemini tạo nội dung và **bắt buộc trả JSON**

Các loại request (ví dụ):
- `exercise_generation`: sinh câu hỏi bài tập
- `topic_generation`: sinh catalog chủ đề
- `writing_prompt_generation`: sinh đề viết mỗi ngày
- `writing_evaluation`: chấm bài viết + corrected_text + tags lỗi
- `flashcard_generation`: sinh flashcards theo topic
- `speaking_prompt_generation`: sinh speaking prompts
- `explain_wrong_answer`: giải thích vì sao sai
- `tutor_chat` + `summarize_chat`: chat tutor + tổng kết phiên

### 6.2) Thiết kế prompt & định dạng JSON
- Prompt luôn yêu cầu **strict JSON only** và kèm “Required JSON format”
- `response_mime_type="application/json"` để giảm lỗi định dạng
- Có bước strip ```json ... ``` nếu model trả về fenced code block

### 6.3) Độ tin cậy (fallback / retry)
- Nếu AI lỗi mạng/quota/timeout: web_app dùng **fallback** để ứng dụng vẫn chạy
- Có retry/backoff + cooldown khi AI lỗi liên tục
- Có rate-limit theo user cho các thao tác tốn AI (writing, flashcards, chat, refresh prompt…)

### 6.4) Giới hạn
- Nội dung AI có thể sai/không nhất quán → hệ thống ưu tiên “trải nghiệm học liên tục” bằng fallback và lưu lịch sử để theo dõi tiến bộ.

---

## 7) Xử lý trường hợp biên (edge cases)

Đã có:
- Validate input ở nhiều route (ví dụ: writing quá ngắn, thiếu message chat, topic flashcard rỗng…)
- Rate-limit theo người dùng cho tác vụ AI
- AI service kiểm tra `X-API-KEY` (nếu có `SECRET_KEY`)
- AI service parse JSON “robust” (đọc raw body khi header không chuẩn)
- Fallback khi AI trả dữ liệu không hợp lệ

Ghi chú khi demo/chấm:
- Một số endpoint giả định payload luôn hợp lệ; khi demo nên thao tác đúng luồng UI.

---

## 8) Hướng dẫn demo nhanh (để chứng minh “cài đặt hàm”)

1) Register/Login
2) Dashboard: xem streak, roadmap AI, gợi ý ôn tập
3) “Làm bài ngay”:
   - Làm bài, nộp, xem điểm, xem next_review_date
   - Bấm “Tại sao sai?” để gọi AI giải thích
4) Writing:
   - Xem đề hôm nay, nộp bài, xem corrected_text + feedback + error_tags
5) Flashcards:
   - Generate set theo topic, review thẻ (chọn quality) để cập nhật lịch SM-2
6) Speaking:
   - Generate prompts mới
7) Chat Tutor:
   - Chat vài câu, End session để xem summary + key errors

---

