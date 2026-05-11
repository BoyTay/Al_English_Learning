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

    if request_type == 'writing_prompt_generation':
        date_key = (data.get('date_key') or '').strip()
        prompt = f"""
Role: You are an English writing coach.
Task: Create one practical daily writing prompt.
Context date: {date_key or 'today'}.
Requirements:
- Return strict JSON only.
- No markdown and no explanation outside JSON.
- Keep topic concise and practical for A2-B1 learners.
- Instructions should target 120-180 words.
- sample_outline should have 3-4 short bullets.

Required JSON format:
{{
  "topic": "...",
  "instructions": "...",
  "sample_outline": ["...", "...", "..."]
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
            return jsonify({"error": error_text, "message": "Failed to generate writing prompt."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to generate writing prompt."}), 500

    if request_type == 'writing_evaluation':
        user_text = (data.get('user_text') or '').strip()
        topic = (data.get('topic') or 'General writing').strip()
        instructions = (data.get('instructions') or '').strip()
        if not user_text:
            return jsonify({"error": "user_text is required"}), 400

        prompt = f"""
Role: You are an English writing evaluator.
Task: Evaluate the learner text and provide actionable feedback.
Topic: {topic}
Instructions: {instructions}
Learner text:
\"\"\"
{user_text}
\"\"\"

Requirements:
- Return strict JSON only.
- No markdown and no explanation outside JSON.
- Score must be from 0 to 10.
- corrected_text must preserve original idea but improve grammar and naturalness.
- strengths and improvement_points each contain 2-5 concise items.
- error_tags must be short lowercase tags (e.g. tense, articles, prepositions, word_choice, agreement, punctuation).

Required JSON format:
{{
  "score": 0,
  "feedback_summary": "...",
  "corrected_text": "...",
  "strengths": ["...", "..."],
  "improvement_points": ["...", "..."],
  "error_tags": ["...", "..."]
}}
"""
        try:
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
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
            return jsonify({"error": error_text, "message": "Failed to evaluate writing."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to evaluate writing."}), 500

    if request_type == 'flashcard_generation':
        topic = (data.get('topic') or 'Daily vocabulary').strip()
        card_count = int(data.get('card_count', 10))
        card_count = max(3, min(20, card_count))
        prompt = f"""
Role: You are an English vocabulary coach.
Task: Generate smart flashcards for the learner.
Topic focus: {topic}
Requirements:
- Return strict JSON only.
- No markdown and no explanation outside JSON.
- Create exactly {card_count} cards.
- Keep terms practical for A2-B1 learners.
- definition must be short and clear (English).
- meaning_vi must be a short Vietnamese translation.
- part_of_speech must be one of: noun, verb, adjective, adverb, phrase.
- example_sentence should use natural daily English.
- pronunciation_hint should be easy to read for Vietnamese learners.
- ipa_pronunciation should be standard IPA, e.g. /ˈpæs.pɔːrt/.
- image_hint should be a short phrase describing a simple visual idea.
- image_url can be empty string if no image.

Required JSON format:
{{
  "cards": [
    {{
      "term": "...",
      "definition": "...",
      "meaning_vi": "...",
      "part_of_speech": "noun|verb|adjective|adverb|phrase",
      "example_sentence": "...",
      "pronunciation_hint": "...",
      "ipa_pronunciation": "...",
      "image_hint": "...",
      "image_url": ""
    }}
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
            return jsonify({"error": error_text, "message": "Failed to generate flashcards."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to generate flashcards."}), 500

    if request_type == 'speaking_prompt_generation':
        prompt_count = int(data.get('prompt_count', 6))
        prompt_count = max(3, min(12, prompt_count))
        prompt = f"""
Role: You are an English speaking coach.
Task: Create short speaking/listening practice prompts for learners.
Requirements:
- Return strict JSON only.
- No markdown and no explanation outside JSON.
- Create exactly {prompt_count} prompts.
- Each prompt must have topic, sentence, and difficulty.
- sentence should be practical and natural daily English.
- difficulty must be one of: easy, medium, hard.

Required JSON format:
{{
  "prompts": [
    {{
      "topic": "...",
      "sentence": "...",
      "difficulty": "easy|medium|hard"
    }}
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
            if "UNAVAILABLE" in error_text:
                return jsonify({
                    "error": error_text,
                    "message": "Gemini is temporarily unavailable."
                }), 503
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": error_text, "message": "Failed to generate speaking prompts."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to generate speaking prompts."}), 500

    if request_type == 'answer_explanation':
        question = (data.get('question') or '').strip()
        options = data.get('options') if isinstance(data.get('options'), list) else []
        options_text = ", ".join([str(o) for o in options])
        correct_answer = (data.get('correct_answer') or '').strip()
        user_answer = (data.get('user_answer') or '').strip()
        topic = (data.get('topic') or 'General English').strip()
        question_type = (data.get('question_type') or 'grammar').strip().lower()

        prompt = f"""
Role: You are a patient English tutor.
Task: Explain why the learner's answer is wrong in simple Vietnamese, and give a memory tip.
Context:
- Topic: {topic}
- Question type: {question_type}
- Question: {question}
- Options: {options_text}
- Correct answer: {correct_answer}
- Learner answer: {user_answer}

Return strict JSON only:
{{
  "summary": "...",
  "why_wrong": "...",
  "memory_tip": "..."
}}
"""
        try:
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
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
                return jsonify({"error": error_text, "message": "Gemini quota exhausted."}), 429
            if "UNAVAILABLE" in error_text:
                return jsonify({"error": error_text, "message": "Gemini is temporarily unavailable."}), 503
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": error_text, "message": "Failed to explain answer."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to explain answer."}), 500

    if request_type == 'tutor_chat':
        user_message = (data.get('user_message') or '').strip()
        history = data.get('message_history') if isinstance(data.get('message_history'), list) else []
        if not user_message:
            return jsonify({"error": "user_message is required"}), 400

        history_lines = []
        for msg in history[-20:]:
            if not isinstance(msg, dict):
                continue
            role = (msg.get('role') or 'user').strip().lower()
            content = (msg.get('content') or '').strip()
            if content:
                history_lines.append(f"{role}: {content}")
        history_text = "\n".join(history_lines)

        prompt = f"""
Role: You are an English conversation partner and tutor.
Task: Reply naturally to the learner in English, and gently point out major mistakes.
Conversation history:
{history_text}
Learner message:
{user_message}

Return strict JSON only:
{{
  "assistant_reply": "...",
  "corrections": ["short correction note 1", "short correction note 2"],
  "error_tags": ["tense", "word_choice"]
}}
"""
        try:
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.6,
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
                return jsonify({"error": error_text, "message": "Gemini quota exhausted."}), 429
            if "UNAVAILABLE" in error_text:
                return jsonify({"error": error_text, "message": "Gemini is temporarily unavailable."}), 503
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": error_text, "message": "Failed to chat with tutor."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to chat with tutor."}), 500

    if request_type == 'tutor_chat_summary':
        history = data.get('message_history') if isinstance(data.get('message_history'), list) else []
        history_lines = []
        for msg in history[-40:]:
            if not isinstance(msg, dict):
                continue
            role = (msg.get('role') or 'user').strip().lower()
            content = (msg.get('content') or '').strip()
            if content:
                history_lines.append(f"{role}: {content}")
        history_text = "\n".join(history_lines)

        prompt = f"""
Role: You are an English tutor.
Task: Summarize learner mistakes from this chat and provide compact error tags.
Chat history:
{history_text}

Return strict JSON only:
{{
  "summary": "...",
  "key_errors": ["tense", "articles", "word_choice"]
}}
"""
        try:
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
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
                return jsonify({"error": error_text, "message": "Gemini quota exhausted."}), 429
            if "UNAVAILABLE" in error_text:
                return jsonify({"error": error_text, "message": "Gemini is temporarily unavailable."}), 503
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": error_text, "message": "Failed to summarize chat."}), 500
        except Exception as e:
            print(f"\n================ LỖI TẠI AI MICROSERVICE ================", flush=True)
            traceback.print_exc()
            print(f"================================================\n", flush=True)
            return jsonify({"error": str(e), "message": "Failed to summarize chat."}), 500

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
