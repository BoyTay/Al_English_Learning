import os
import json
import traceback
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from google.genai.errors import ClientError

load_dotenv()
app = Flask(__name__)
app.config['TRUSTED_HOSTS'] = [
    'localhost',
    '127.0.0.1',
    'ai_service',
    'ai_service:5001',
]

api_key = os.environ.get('GEMINI_API_KEY')
if not api_key:
    print("WARNING: GEMINI_API_KEY is not set in environment variables.")

# Khởi tạo bảo mật kết nối Web App - AI Service
API_SECRET_KEY = os.environ.get('SECRET_KEY')

@app.route('/generate', methods=['POST'])
def generate_exercise():
    # Yêu cầu Header X-API-KEY phải khớp với SECRET_KEY nội bộ
    req_key = request.headers.get('X-API-KEY')
    if API_SECRET_KEY and req_key != API_SECRET_KEY:
        return jsonify({"error": "Unauthorized: Missing or Invalid X-API-KEY"}), 401
        
    # Robustly parse JSON and fall back to raw-body parsing when clients send
    # a valid JSON string with imperfect headers.
    try:
        data = request.get_json(silent=True, force=False)
    except Exception:
        data = None

    if data is None:
        raw_body = request.get_data(as_text=True) or ''
        if raw_body.strip():
            try:
                data = json.loads(raw_body)
            except Exception:
                data = None

    if not data:
        app.logger.warning("[ai_service] /generate called with no/invalid JSON payload")
        app.logger.warning("[ai_service] Request headers: %s", dict(request.headers))
        try:
            app.logger.warning("[ai_service] Raw body: %s", request.get_data(as_text=True))
        except Exception:
            pass
        data = {}

    topic = data.get('topic') or data.get('topic_name') or 'Basic Grammar'
    level = data.get('level', 'Intermediate')
    exercise_type = data.get('exercise_type', 'Multiple Choice')
    mode = (data.get('mode') or 'random').strip().lower()
    topic_type = (data.get('topic_type') or 'grammar').strip().lower()
    subtopic_focus = (data.get('subtopic_focus') or '').strip()
    streak_days = data.get('streak_days', 0)
    request_type = data.get('request_type', 'exercise_generation')

    if request_type == 'topic_generation':
        topic_count = int(data.get('topic_count', 10))
        prompt = f"""
Role: Bạn là AI thiết kế khung nội dung cho ứng dụng học tiếng Anh.
Task: Sinh danh sách chủ đề học tiếng Anh phù hợp cho người mới đến trung cấp.
YÊU CẦU:
- Trả về đúng JSON.
- Không markdown, không giải thích ngoài JSON.
- Tạo đúng {topic_count} chủ đề khác nhau.
- Mỗi chủ đề gồm name và description.
- Tên chủ đề phải ngắn gọn, rõ nghĩa, không trùng nhau.
- Chủ đề phải hữu ích cho luyện tiếng Anh thực tế.
- Mỗi topic phải có topic_type là "grammar" hoặc "vocabulary".

Format JSON bắt buộc:
{{
  "topics": [
        {{"name": "...", "description": "...", "topic_type": "grammar|vocabulary"}}
  ]
}}
"""

        try:
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    response_mime_type="application/json",
                )
            )

            text = response.text.strip()
            if text.startswith('```json'):
                text = text[7:]
            if text.startswith('```'):
                text = text[3:]
            if text.endswith('```'):
                text = text[:-3]

            result = json.loads(text.strip())
            return jsonify(result)

        except ClientError as e:
            error_text = str(e)
            if "RESOURCE_EXHAUSTED" in error_text or "quota" in error_text.lower():
                return jsonify({
                    "error": error_text,
                    "message": "Gemini quota exhausted."
                }), 429
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": error_text, "message": "Failed to generate topic catalog from Gemini."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to generate topic catalog from Gemini."}), 500

    if request_type == 'roadmap_generation':
        prompt = """
Role: Bạn là AI thiết kế lộ trình học tiếng Anh.
Task: Tạo roadmap gồm 2 nhóm: grammar và vocabulary.
YÊU CẦU:
- Trả về đúng JSON.
- Không markdown, không giải thích ngoài JSON.
- Mỗi nhóm có đúng 6 mục.
- Mỗi mục có name và desc ngắn gọn.

Format JSON bắt buộc:
{
  "grammar_roadmap": [
    {"name": "...", "desc": "..."}
  ],
  "vocabulary_roadmap": [
    {"name": "...", "desc": "..."}
  ]
}
"""

        try:
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    response_mime_type="application/json",
                )
            )

            text = response.text.strip()
            if text.startswith('```json'):
                text = text[7:]
            if text.startswith('```'):
                text = text[3:]
            if text.endswith('```'):
                text = text[:-3]

            result = json.loads(text.strip())
            return jsonify(result)

        except ClientError as e:
            error_text = str(e)
            if "RESOURCE_EXHAUSTED" in error_text or "quota" in error_text.lower():
                return jsonify({
                    "error": error_text,
                    "message": "Gemini quota exhausted."
                }), 429
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": error_text, "message": "Failed to generate roadmap from Gemini."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to generate roadmap from Gemini."}), 500

    if mode not in {"random"}:
        mode = "random"

    mode_instructions = {
        "random": "Mỗi câu random loại grammar/vocabulary độc lập, không cần cùng loại trong một bài.",
    }

    prompt = f"""
Role: Bạn là AI tạo bài tập tiếng Anh.
Task: Tạo bài tập tiếng Anh dựa trên topic và trình độ.
Context: User đang yếu ở topic: {topic}. Trình độ: {level}. Loại bài: {exercise_type}. Chuỗi học liên tục: {streak_days} ngày. Topic type: {topic_type}. Mode: {mode}.

Instruction:
- Tạo 5 câu hỏi.
- Rule mode: {mode_instructions.get(mode, mode_instructions['random'])}
- Nếu subtopic_focus có giá trị thì ưu tiên bám sát tiểu chủ đề đó: {subtopic_focus or 'không chỉ định'}.
- Format strict JSON theo cấu trúc dưới đây. Bắt buộc từ khóa là questions, question, options, answer, explanation. KHÔNG TRẢ VỀ BẤT KỲ VĂN BẢN HOẶC MARKDOWN NÀO KHÁC.
{{
    "mode": "{mode}",
  "questions": [
    {{
            "question_type": "grammar|vocabulary",
            "subtopic": "Tên tiểu chủ đề cụ thể, ví dụ: Conditionals / Passive Voice / Phrasal Verbs",
      "question": "...",
      "options": ["option1 text", "option2 text", "option3 text", "option4 text"],
      "answer": "exact option text that is correct",
      "explanation": "..."
    }}
  ]
}}
- YÊU CẦU QUAN TRỌNG: 
  * Trường "answer" PHẢI là NỘI DUNG CHÍNH XÁC của một trong các options, KHÔNG PHẢI chữ cái A/B/C/D
    * Trường "question_type" chỉ được là "grammar" hoặc "vocabulary"
    * Trường "subtopic" bắt buộc có nội dung ngắn, rõ ràng
  * Ví dụ nếu options là ["know", "knows", "are knowing", "is knowing"], thì answer phải là "knows" (nội dung đầy đủ), KHÔNG ĐƯỢC là "B"
  * Đúng ngữ pháp, phù hợp trình độ, không trùng câu hỏi
"""

    try:
        # Initialize Client mới để tự động sử dụng GEMINI_API_KEY từ env
        client = genai.Client()
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            )
        )
        
        text = response.text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
            
        result = json.loads(text.strip())
        if isinstance(result, dict):
            result["mode"] = mode
            questions = result.get("questions") or []
            for q in questions:
                if not isinstance(q, dict):
                    continue
                q_type = (q.get("question_type") or topic_type or "grammar").strip().lower()
                if q_type not in {"grammar", "vocabulary"}:
                    q_type = "grammar"
                q["question_type"] = q_type
                subtopic = (q.get("subtopic") or subtopic_focus or topic).strip()
                q["subtopic"] = subtopic if subtopic else "General"
        return jsonify(result)
        
    except ClientError as e:
        error_text = str(e)
        if "RESOURCE_EXHAUSTED" in error_text or "quota" in error_text.lower():
            return jsonify({
                "error": error_text,
                "message": "Gemini quota exhausted."
            }), 429
        print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
        traceback.print_exc()
        print(f"================================================\n", flush=True)
        return jsonify({"error": error_text, "message": "Failed to generate from Gemini."}), 500
    except Exception as e:
        print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
        traceback.print_exc()
        print(f"================================================\n", flush=True)
        return jsonify({"error": str(e), "message": "Failed to generate from Gemini."}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)
