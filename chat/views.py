import json
import uuid
import requests
import io
try:
    import PyPDF2
    import docx
except ImportError:
    pass

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import ChatSession, Message

def index(request):
    sessions = ChatSession.objects.all()[:20]
    return render(request, 'chat/index.html', {'sessions': sessions})


def get_session(request, session_id):
    try:
        session = ChatSession.objects.get(session_id=session_id)
        messages = session.messages.all()
        data = {
            'session_id': session.session_id,
            'title': session.title,
            'messages': [
                {'role': m.role, 'content': m.content}
                for m in messages
            ]
        }
        return JsonResponse(data)
    except ChatSession.DoesNotExist:
        return JsonResponse({'error': 'Session not found'}, status=404)


def new_session(request):
    session_id = str(uuid.uuid4())[:8]
    session = ChatSession.objects.create(session_id=session_id)
    return JsonResponse({'session_id': session.session_id, 'title': session.title})


def delete_session(request, session_id):
    try:
        session = ChatSession.objects.get(session_id=session_id)
        session.delete()
        return JsonResponse({'success': True})
    except ChatSession.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)


@csrf_exempt
def chat(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    user_message = data.get('message', '').strip()
    session_id = data.get('session_id')

    if not user_message:
        return JsonResponse({'error': 'Empty message'}, status=400)

    if session_id:
        session, _ = ChatSession.objects.get_or_create(
            session_id=session_id,
            defaults={'title': user_message[:50]}
        )
    else:
        session = ChatSession.objects.create(
            session_id=str(uuid.uuid4())[:8],
            title=user_message[:50]
        )

    if session.messages.count() == 0:
        session.title = user_message[:50]
        session.save()

    Message.objects.create(session=session, role='user', content=user_message)

    # Build Groq conversation history
    history = list(session.messages.all())
    groq_messages = []
    for msg in history:
        groq_messages.append({
            'role': msg.role,
            'content': msg.content
        })

    # Call Groq API
    try:
        api_key = settings.GROQ_API_KEY
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": settings.GROQ_MODEL,
            "messages": groq_messages,
            "temperature": 0.7,
            "max_tokens": 2048,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        ai_reply = result['choices'][0]['message']['content'].strip()

    except requests.exceptions.Timeout:
        ai_reply = "Groq took too long to respond. Please try again."
    except requests.exceptions.HTTPError as e:
        ai_reply = f"Groq API Error: {response.text}"
    except requests.exceptions.ConnectionError:
        ai_reply = "Cannot connect to Groq API. Check your internet connection."
    except KeyError:
        ai_reply = "Unexpected response from Groq. Please try again."
    except Exception as e:
        ai_reply = f"Error: {str(e)}"

    Message.objects.create(session=session, role='assistant', content=ai_reply)

    return JsonResponse({
        'reply': ai_reply,
        'session_id': session.session_id,
        'title': session.title
    })


def get_sessions(request):
    sessions = ChatSession.objects.all()[:30]
    data = [{'session_id': s.session_id, 'title': s.title} for s in sessions]
    return JsonResponse({'sessions': data})

@csrf_exempt
def whisper_audio(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'error': 'No audio file provided'}, status=400)
    
    target_language = request.POST.get('target_language', 'Original')

    api_key = settings.GROQ_API_KEY
    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    # Decide whisper endpoint. 
    # If English, we can directly let Whisper do it via translations endpoint.
    if target_language == 'English':
        url = "https://api.groq.com/openai/v1/audio/translations"
    else:
        # We use transcriptions for 'Original' or for capturing text to later translate via LLM
        url = "https://api.groq.com/openai/v1/audio/transcriptions"

    files = {
        'file': (audio_file.name, audio_file.read(), audio_file.content_type)
    }
    data = {
        'model': 'whisper-large-v3'
    }

    try:
        response = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        text = result.get('text', '').strip()

        # If LLM translation is required (i.e. not Original and not natively English translated)
        if target_language not in ['Original', 'English'] and text:
            llm_url = "https://api.groq.com/openai/v1/chat/completions"
            llm_headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            llm_payload = {
                "model": settings.GROQ_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": f"You are a fast, high-quality translation engine. Translate the provided text into {target_language}. Respond ONLY with the single translated text string. No quotes, no conversational filler, no explanations."
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ],
                "temperature": 0.3, # low temperature for accurate translation
                "max_tokens": 1024,
            }
            llm_response = requests.post(llm_url, headers=llm_headers, json=llm_payload, timeout=30)
            llm_response.raise_for_status()
            llm_result = llm_response.json()
            text = llm_result['choices'][0]['message']['content'].strip()

        return JsonResponse({'text': text})
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                if 'error' in error_data and 'message' in error_data['error']:
                    error_msg = error_data['error']['message']
            except:
                pass
        return JsonResponse({'error': error_msg}, status=500)

@csrf_exempt
def document_upload(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    doc_file = request.FILES.get('document')
    if not doc_file:
        return JsonResponse({'error': 'No document file provided'}, status=400)
    
    filename = doc_file.name.lower()
    extracted_text = ""

    try:
        if filename.endswith('.pdf'):
            reader = PyPDF2.PdfReader(doc_file)
            for page in reader.pages:
                extracted_text += page.extract_text() + "\n"
        elif filename.endswith('.docx'):
            doc = docx.Document(doc_file)
            for para in doc.paragraphs:
                extracted_text += para.text + "\n"
        elif filename.endswith('.txt'):
            extracted_text = doc_file.read().decode('utf-8')
        else:
            return JsonResponse({'error': 'Unsupported file type. Use .pdf, .docx, or .txt'}, status=400)
    except Exception as e:
        return JsonResponse({'error': f"Failed to parse document: {str(e)}"}, status=500)

    if not extracted_text.strip():
        return JsonResponse({'error': 'Could not extract any text from the document.'}, status=400)

    return JsonResponse({'text': extracted_text.strip()})


@csrf_exempt
def generate_prompt(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    task = data.get('task', '').strip()
    style = data.get('style', 'detailed')  # detailed, concise, creative

    if not task:
        return JsonResponse({'error': 'No task provided'}, status=400)

    style_instructions = {
        'detailed': 'Generate a HIGHLY DETAILED, comprehensive prompt with all sections filled thoroughly. Include specific technical details, examples, and edge cases.',
        'concise': 'Generate a CONCISE but effective prompt. Keep each section brief but impactful. Focus on clarity and precision.',
        'creative': 'Generate a CREATIVE and imaginative prompt. Use vivid language, unique angles, and innovative approaches. Think outside the box.',
    }

    system_prompt = f"""You are a MASTER PROMPT ENGINEER — the world's best at crafting AI prompts. Your job is to take a user's rough task/idea and transform it into a perfectly structured, professional-grade prompt that will produce outstanding results when used with any AI model.

STYLE: {style_instructions.get(style, style_instructions['detailed'])}

You MUST output the prompt in the following EXACT structure using markdown headers. Do NOT skip any section:

# 🎯 Role & Persona
Define who the AI should act as (expert role, experience level, personality)

# 📋 Context & Background  
Provide relevant background information and context for the task

# ✅ Task Description
Clear, specific description of what needs to be accomplished

# 📌 Requirements & Specifications
Bullet-pointed list of specific requirements, features, or criteria

# 📐 Output Format & Structure
Describe exactly how the output should be formatted and organized

# ⚠️ Constraints & Guidelines
List any limitations, things to avoid, or important guidelines

# 💡 Examples & References
Provide example outputs or reference points for quality benchmarks

# 🔑 Success Criteria
Define what makes the output excellent vs. mediocre

IMPORTANT RULES:
- Make the prompt ACTIONABLE and SPECIFIC — no vague instructions
- Include concrete details, metrics, and measurable outcomes where possible
- The generated prompt should be ready to copy-paste directly into any AI tool
- Use professional language but keep it clear and accessible
- Each section should have 2-5 detailed points minimum
- Do NOT wrap the output in a code block — output raw markdown directly
- Do NOT add any text before or after the structured prompt — ONLY output the prompt itself"""

    try:
        api_key = settings.GROQ_API_KEY
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Generate a perfect, professional prompt for this task: {task}"}
            ],
            "temperature": 0.8,
            "max_tokens": 3000,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
        prompt_text = result['choices'][0]['message']['content'].strip()

        return JsonResponse({
            'prompt': prompt_text,
            'task': task,
            'style': style,
        })

    except requests.exceptions.Timeout:
        return JsonResponse({'error': 'Groq took too long. Try again.'}, status=504)
    except requests.exceptions.HTTPError:
        return JsonResponse({'error': f'Groq API Error: {response.text}'}, status=502)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
