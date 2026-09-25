from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
from flask_session import FileSystemSessionInterface
import requests
import json
import secrets
from datetime import datetime
import os
import base64
from werkzeug.utils import secure_filename
import io
import mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import hashlib

# Some libraries are optional, so we check if they're installed
try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    print("Warning: Pillow not available. Image processing will be disabled.")

try:
    import PyPDF2
    PYPDF2_AVAILABLE = True
except ImportError:
    PYPDF2_AVAILABLE = False
    print("Warning: PyPDF2 not available. PDF processing will be disabled.")

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("Warning: python-docx not available. DOCX processing will be disabled.")

# Load settings from a .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# Flask app configuration
app.config['SECRET_KEY'] = os.environ.get('NOVA_SECRET_KEY') or secrets.token_hex(32)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = './flask_session'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = False
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_NAME'] = 'nova_session'
app.config['SESSION_FILE_THRESHOLD'] = 500
app.config['SESSION_FILE_MODE'] = 0o600
app.config['SESSION_KEY_PREFIX'] = 'session:'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'

# Custom session handler to make sure session IDs work properly
class StringSessionInterface(FileSystemSessionInterface):
    def save_session(self, app, session, response):
        """Make sure session IDs are always strings, not bytes, and force save"""
        if hasattr(session, 'sid'):
            session_id = session.sid
            if isinstance(session_id, bytes):
                session_id = session_id.decode('utf-8')
            elif not isinstance(session_id, str):
                session_id = str(session_id)
            
            session.sid = session_id
        
        # Always mark as modified to ensure Flask saves nested changes
        session.modified = True
        
        # Force save by accessing session data
        _ = session.get('current_chat', {})
        _ = session.get('preferred_mode', 'balanced')
        
        return super().save_session(app, session, response)

# Set up the session system
session_interface = StringSessionInterface(
    cache_dir=app.config['SESSION_FILE_DIR'],
    threshold=app.config['SESSION_FILE_THRESHOLD'],
    mode=app.config['SESSION_FILE_MODE'],
    key_prefix=app.config['SESSION_KEY_PREFIX']
)
app.session_interface = session_interface

# Make sure we have directories for sessions and uploads
os.makedirs('./flask_session', exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# What file types we accept
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
ALLOWED_DOCUMENT_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt', 'md'}

# Login credentials come from environment variables (see .env.example).
# If no password is set, a random one is generated and printed at startup.
NOVA_USERNAME = os.environ.get('NOVA_USERNAME', 'admin')
NOVA_PASSWORD = os.environ.get('NOVA_PASSWORD')
if not NOVA_PASSWORD:
    NOVA_PASSWORD = secrets.token_urlsafe(12)
    print(f"🔐 NOVA_PASSWORD not set. Generated password for this run: {NOVA_PASSWORD}")

def check_credentials(username, password):
    """Constant-time comparison so login timing doesn't leak information"""
    user_ok = secrets.compare_digest(str(username or '').encode('utf-8'), NOVA_USERNAME.encode('utf-8'))
    pass_ok = secrets.compare_digest(str(password or '').encode('utf-8'), NOVA_PASSWORD.encode('utf-8'))
    return user_ok and pass_ok

# Where Ollama is running
OLLAMA_API_URL = os.environ.get('OLLAMA_API_URL', 'http://localhost:11434')

# Global model cache — loaded once at startup, refreshed on demand
_models_cache = []
_models_cache_time = 0
MODELS_CACHE_TTL = 60  # seconds before cache expires

def get_cached_models():
    """Return models from cache, refresh if older than TTL"""
    global _models_cache, _models_cache_time
    now = time.time()
    if _models_cache and (now - _models_cache_time) < MODELS_CACHE_TTL:
        return _models_cache  # Return instantly from cache
    try:
        response = requests.get(f'{OLLAMA_API_URL}/api/tags', timeout=5)
        if response.status_code == 200:
            raw = response.json().get('models', [])
            _models_cache = [
                {
                    'name': m.get('name', ''),
                    'size': m.get('size', 0),
                    'modified_at': m.get('modified_at', ''),
                }
                for m in raw if m.get('name')
            ]
            _models_cache_time = now
            print(f"✅ Model cache refreshed: {len(_models_cache)} models")
    except Exception as e:
        print(f"⚠️ Could not refresh model cache: {e}")
    return _models_cache

# All the models Nova can use - your exact installed models
AVAILABLE_MODELS = [
    {'name': 'qwen2.5:3b',           'description': 'Qwen2.5 3B - Fast routing & quick responses',        'size': '~1.9GB'},
    {'name': 'qwen2.5:7b-instruct',  'description': 'Qwen2.5 7B Instruct - Best general chat',            'size': '~4.7GB'},
    {'name': 'qwen2.5-coder:7b',     'description': 'Qwen2.5 Coder 7B - Primary coding model',            'size': '~4.7GB'},
    {'name': 'qwen2.5:14b',          'description': 'Qwen2.5 14B - Strong reasoning & complex tasks',     'size': '~9GB'},
    {'name': 'gemma3:4b',            'description': 'Gemma3 4B - Task detection & routing',               'size': '~3.3GB'},
    {'name': 'llama3.1:8b',          'description': 'Llama 3.1 8B - Creative writing & analysis',         'size': '~4.9GB'},
    {'name': 'llava:7b',             'description': 'LLaVA 7B - Image understanding (vision)',             'size': '~4.7GB'},
    {'name': 'deepseek-coder:6.7b',  'description': 'DeepSeek Coder 6.7B - Code alternative',             'size': '~3.8GB'},
    {'name': 'gpt-oss:20b',          'description': 'GPT-OSS 20B - Complex reasoning & hard questions',   'size': '~13GB'},
    {'name': 'qwen3-coder:latest',   'description': 'Qwen3 Coder - Heavy coding tasks',                   'size': '~18GB'},
    {'name': 'nomic-embed-text',     'description': 'Nomic Embed - Embeddings',                           'size': '~0.3GB'},
]

RECOMMENDED_MODELS = AVAILABLE_MODELS.copy()

# Nova uses its own model to decide which model to use for each task
# We use smaller, faster models here so routing decisions happen quickly
NOVA_ROUTER_MODEL = 'gemma3:4b'
NOVA_ROUTER_BACKUP = 'qwen2.5:3b'
NOVA_ROUTER_FALLBACK = 'qwen2.5:7b-instruct'

# Models that can see and understand images
VISION_MODELS = ['llava']

# How Nova picks models for different types of tasks
ROUTING_CONFIG = {
    'coding': {
        'models': ['qwen2.5-coder:7b', 'deepseek-coder:6.7b', 'qwen3-coder:latest'],
        'reasoning': 'Coding task — using Qwen2.5-Coder, DeepSeek-Coder, or Qwen3-Coder.'
    },
    'math': {
        'models': ['qwen2.5:14b', 'gpt-oss:20b', 'qwen2.5:7b-instruct'],
        'reasoning': 'Math task — using Qwen2.5-14B or GPT-OSS-20B for strong reasoning.'
    },
    'creative': {
        'models': ['llama3.1:8b', 'qwen2.5:7b-instruct', 'qwen2.5:14b'],
        'reasoning': 'Creative task — using Llama 3.1 or Qwen2.5 for writing quality.'
    },
    'complex': {
        'models': ['gpt-oss:20b', 'qwen2.5:14b', 'qwen2.5:7b-instruct'],
        'reasoning': 'Complex task — using GPT-OSS-20B or Qwen2.5-14B for deep analysis.'
    },
    'simple': {
        'models': ['qwen2.5:3b', 'gemma3:4b'],
        'reasoning': 'Simple question — using smallest fastest model.'
    },
    'general': {
        'models': ['qwen2.5:7b-instruct', 'qwen2.5:14b', 'llama3.1:8b'],
        'reasoning': 'General query — using Qwen2.5-7B-Instruct as primary.'
    },
    'vision': {
        'models': ['llava:7b'],
        'reasoning': 'Image task — using LLaVA-7B for vision understanding.'
    }
}

# Different ways Nova can work
MODE_CONFIG = {
    'quick': {
        'num_models': 1,
        'strategy': 'single',
        'description': 'Fastest response with single best model'
    },
    'balanced': {
        'num_models': 1,
        'strategy': 'routed',
        'description': 'Smart router selects optimal model based on task'
    },
    'deep': {
        'num_models': 3,
        'strategy': 'ensemble',
        'description': 'Multiple models provide diverse perspectives'
    },
    'expert': {
        'num_models': 'all',
        'strategy': 'voting',
        'description': 'All available models answer in parallel; responses shown together'
    }
}

def allowed_file(filename, file_type='image'):
    """Check if file extension is allowed"""
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    if file_type == 'image':
        return ext in ALLOWED_IMAGE_EXTENSIONS
    elif file_type == 'document':
        return ext in ALLOWED_DOCUMENT_EXTENSIONS
    return False

def is_vision_model(model_name):
    """Check if model supports vision"""
    return any(vm in model_name.lower() for vm in VISION_MODELS)

def process_image(file):
    """Convert image to base64 so models can understand it"""
    if not PILLOW_AVAILABLE:
        raise Exception("Pillow is not installed. Please install it with: pip install Pillow")
    
    try:
        img = Image.open(io.BytesIO(file.read()))
        
        # Make sure it's RGB format
        if img.mode == 'RGBA':
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[3])
            img = rgb_img
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Shrink really big images so they don't slow things down
        max_size = 2048
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # Turn it into base64 text
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        return img_base64
    except Exception as e:
        raise Exception(f"Error processing image: {str(e)}")

def extract_text_from_pdf(file):
    """Extract text from PDF file"""
    if not PYPDF2_AVAILABLE:
        raise Exception("PyPDF2 is not installed. Please install it with: pip install PyPDF2")
    
    try:
        pdf_reader = PyPDF2.PdfReader(file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text.strip()
    except Exception as e:
        raise Exception(f"Error reading PDF: {str(e)}")

def extract_text_from_docx(file):
    """Extract text from DOCX file"""
    if not DOCX_AVAILABLE:
        raise Exception("python-docx is not installed. Please install it with: pip install python-docx")
    
    try:
        doc = Document(file)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        return text.strip()
    except Exception as e:
        raise Exception(f"Error reading DOCX: {str(e)}")

def extract_text_from_txt(file):
    """Read text from a plain text file"""
    try:
        try:
            return file.read().decode('utf-8')
        except UnicodeDecodeError:
            # Some files use different encoding, try latin-1
            file.seek(0)
            return file.read().decode('latin-1')
    except Exception as e:
        raise Exception(f"Error reading text file: {str(e)}")

def process_document(file, filename):
    """Process document and extract text"""
    ext = filename.rsplit('.', 1)[1].lower()
    
    if ext == 'pdf':
        return extract_text_from_pdf(file)
    elif ext in ['docx', 'doc']:
        return extract_text_from_docx(file)
    elif ext in ['txt', 'md']:
        return extract_text_from_txt(file)
    else:
        raise Exception(f"Unsupported document format: {ext}")

# This class figures out which model to use for each request
class ModelRouter:
    """Nova's brain for picking the right model"""
    
    def __init__(self):
        self.available_models = []
        self.load_available_models()
    
    def load_available_models(self):
        """Check what models are installed in Ollama"""
        try:
            response = requests.get(f'{OLLAMA_API_URL}/api/tags', timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                self.available_models = [m['name'] for m in models]
                print(f"Loaded {len(self.available_models)} models from Ollama: {self.available_models}")
            else:
                self.available_models = []
                print(f"Ollama returned error status {response.status_code}")
        except requests.exceptions.ConnectionError:
            self.available_models = []
            print("Cannot connect to Ollama - is it running?")
        except Exception as e:
            self.available_models = []
            print(f"Error loading models: {e}")
    
    def get_nova_router_model(self):
        """Find which model Nova should use for making decisions"""
        if NOVA_ROUTER_MODEL in self.available_models:
            return NOVA_ROUTER_MODEL
        elif NOVA_ROUTER_BACKUP in self.available_models:
            return NOVA_ROUTER_BACKUP
        elif NOVA_ROUTER_FALLBACK in self.available_models:
            return NOVA_ROUTER_FALLBACK
        elif self.available_models:
            # If none of our preferred models are available, use the smallest one we have
            small_models = [m for m in self.available_models if '3b' in m or '4b' in m]
            if small_models:
                return small_models[0]
            return self.available_models[0]
        return None
    
    def detect_task_type_with_ai(self, message, has_images=False):
        """Ask Nova's router model what kind of task this is"""
        if has_images:
            return 'vision', 'Image detected - using vision model'
        
        nova_model = self.get_nova_router_model()
        if not nova_model:
            return self.detect_task_type(message, has_images), 'Keyword-based detection (Nova router model not available)'
        
        # Make sure we're working with text, not bytes
        if isinstance(message, bytes):
            message = message.decode('utf-8', errors='ignore')
        elif not isinstance(message, str):
            message = str(message)
        
        # Ask Nova's router model to figure out what type of task this is
        analysis_prompt = f"""You are Nova, an intelligent AI assistant that analyzes user requests to select the best model for each task.

Available task types:
- coding: Programming, code generation, debugging, software development
- math: Mathematical problems, calculations, equations, proofs
- creative: Creative writing, stories, poems, imaginative content
- complex: Complex reasoning, analysis, comparisons, decision-making
- simple: Simple factual questions, definitions, basic information
- general: General conversation, questions, general queries
- vision: Image analysis, visual content understanding

User request: "{message}"

Analyze this request and determine the most appropriate task type. Respond with ONLY the task type (one word: coding, math, creative, complex, simple, general, or vision). Do not include any explanation or additional text."""

        try:
            payload = {
                'model': nova_model,
                'prompt': analysis_prompt,
                'stream': False,
                'options': {
                    'temperature': 0.2,
                    'num_predict': 15,
                    'num_ctx': 512
                }
            }
            
            response = requests.post(
                f'{OLLAMA_API_URL}/api/generate',
                json=payload,
                timeout=30  # Increased timeout for routing decision (some models are slower)
            )
            
            if response.status_code == 200:
                result = response.json()
                ai_task_type = result.get('response', '').strip().lower()
                
                # Make sure Nova gave us a valid answer
                valid_types = ['coding', 'math', 'creative', 'complex', 'simple', 'general', 'vision']
                if ai_task_type in valid_types:
                    reasoning = f'Nova ({nova_model}) analyzed the request and identified it as: {ai_task_type}'
                    return ai_task_type, reasoning
                else:
                    # If Nova gave us something weird, use keyword matching instead
                    keyword_task = self.detect_task_type(message, has_images)
                    return keyword_task, f'Nova ({nova_model}) response invalid, using keyword detection: {keyword_task}'
            else:
                # Something went wrong, fall back to keywords
                keyword_task = self.detect_task_type(message, has_images)
                return keyword_task, f'Nova ({nova_model}) unavailable, using keyword detection: {keyword_task}'
        except requests.exceptions.Timeout:
            print(f"Nova router model timeout - falling back to keyword detection")
            # Fallback to keyword detection
            keyword_task = self.detect_task_type(message, has_images)
            return keyword_task, f'Using keyword-based detection (Nova router timeout)'
        except Exception as e:
            print(f"Nova router model error: {e}")
            # Fallback to keyword detection
            keyword_task = self.detect_task_type(message, has_images)
            return keyword_task, f'Using keyword-based detection'
    
    def detect_task_type(self, message, has_images=False):
        """Figure out what kind of task this is by looking for keywords"""
        if has_images:
            return 'vision'
        
        # Make sure we have text to work with
        if isinstance(message, bytes):
            message = message.decode('utf-8', errors='ignore')
        elif not isinstance(message, str):
            message = str(message)
        
        message_lower = message.lower()
        
        # Look for coding-related words
        coding_keywords = [
            'code', 'function', 'python', 'javascript', 'programming', 'debug', 
            'script', 'algorithm', 'class', 'import', 'def ', 'variable', 'syntax',
            'compile', 'execute', 'api', 'framework', 'library', 'bot', 'chatbot',
            'implement', 'create a', 'write a', 'build a', 'develop', 'coding',
            'program', 'software', 'application', 'app', 'html', 'css', 'react',
            'node', 'java', 'c++', 'c#', 'ruby', 'php', 'go', 'rust', 'swift',
            'typescript', 'sql', 'database', 'backend', 'frontend', 'fullstack',
            'github', 'git', 'repository', 'repo', 'commit', 'pull request',
            'docker', 'kubernetes', 'deploy', 'server', 'client', 'endpoint',
            'rest', 'graphql', 'json', 'xml', 'yaml', 'markdown', 'regex',
            'test', 'testing', 'unit test', 'integration', 'ci/cd', 'devops'
        ]
        
        # Also check for common coding phrases
        coding_phrases = [
            'code a', 'write code', 'create code', 'build code', 'write a program',
            'create a program', 'write a script', 'create a script', 'write a function',
            'create a function', 'write a class', 'create a class', 'code example',
            'code snippet', 'source code', 'how to code', 'how to program',
            'programming language', 'code solution', 'implement a', 'develop a'
        ]
        
        # See if this looks like a coding question
        if any(word in message_lower for word in coding_keywords):
            return 'coding'
        
        if any(phrase in message_lower for phrase in coding_phrases):
            return 'coding'
        
        # Check if it's a math problem
        math_keywords = ['calculate', 'solve', 'equation', 'math', 'integral', 'derivative',
                        'formula', 'theorem', 'proof', 'algebra', 'geometry', 'statistics',
                        'probability', 'matrix', 'vector', 'graph', 'plot']
        if any(word in message_lower for word in math_keywords):
            return 'math'
        
        # Maybe they want something creative
        creative_keywords = ['write a story', 'poem', 'creative', 'imagine', 'fiction',
                            'narrative', 'character', 'plot', 'dialogue', 'describe',
                            'write a', 'compose', 'create a story']
        if any(word in message_lower for word in creative_keywords):
            return 'creative'
        
        # Could be something that needs deep thinking
        complex_keywords = ['analyze', 'compare', 'evaluate', 'should i', 'pros and cons',
                           'decision', 'explain why', 'what are the', 'discuss', 'opinion',
                           'recommend', 'suggest', 'which is better', 'advantages']
        if any(word in message_lower for word in complex_keywords):
            return 'complex'
        
        # Or maybe just a simple question
        simple_keywords = ['what is', 'who is', 'when', 'where', 'capital of', 'define',
                          'meaning of', 'what does', 'how many', 'how much']
        if any(word in message_lower for word in simple_keywords):
            return 'simple'
        
        return 'general'
    
    def check_model_availability(self, model_name, available_models_list):
        """See if a model is installed, and tell the user how to install it if not"""
        if model_name in available_models_list:
            return True, None
        
        # Look up info about this model so we can give helpful instructions
        model_info = next((m for m in AVAILABLE_MODELS if m['name'] == model_name), None)
        
        if model_info:
            error_msg = f"Model '{model_name}' is not installed.\n\n"
            error_msg += f"To install it, run:\n"
            error_msg += f"  ollama pull {model_name}\n\n"
            error_msg += f"Description: {model_info.get('description', '')}\n"
            error_msg += f"Size: {model_info.get('size', 'Unknown')}\n\n"
            error_msg += f"Alternatively, install other available models:\n"
            error_msg += f"  ollama pull qwen3:8b\n"
            error_msg += f"  ollama pull llama3.2:8b\n"
            error_msg += f"  ollama pull llava:7b"
        else:
            error_msg = f"Model '{model_name}' is not installed and not in the curated model list.\n\n"
            error_msg += f"To install it, run:\n"
            error_msg += f"  ollama pull {model_name}\n\n"
            error_msg += f"Or install recommended models:\n"
            error_msg += f"  ollama pull qwen3:8b\n"
            error_msg += f"  ollama pull llama3.2:8b"
        
        return False, error_msg
    
    def select_models_with_ai(self, task_type, mode, available_models_list=None, user_message=""):
        """Ask Nova's router model which model would be best for this task"""
        if available_models_list is None:
            available_models_list = self.available_models
        
        nova_model = self.get_nova_router_model()
        if not nova_model or not user_message:
            return self.select_models(task_type, mode, available_models_list)
        
        # Ask Nova to pick the best model
        available_models_str = ', '.join(available_models_list)
        
        selection_prompt = f"""You are Nova, an intelligent AI assistant that selects the best model for each task.

Task type: {task_type}
Available models: {available_models_str}
Mode: {mode}

User request: "{user_message}"

Based on the task type and available models, select the BEST single model for this request. Consider:
- Task complexity and requirements
- Model capabilities and strengths
- Performance and efficiency

Respond with ONLY the model name (e.g., "qwen3:8b"). Do not include any explanation or additional text."""

        try:
            payload = {
                'model': nova_model,
                'prompt': selection_prompt,
                'stream': False,
                'options': {
                    'temperature': 0.2,
                    'num_predict': 25,
                    'num_ctx': 512
                }
            }
            
            response = requests.post(
                f'{OLLAMA_API_URL}/api/generate',
                json=payload,
                timeout=30  # Increased timeout for model selection
            )
            
            if response.status_code == 200:
                result = response.json()
                selected_model = result.get('response', '').strip().strip('"').strip("'")
                
                # Make sure Nova picked a model that's actually available
                if selected_model in available_models_list:
                    reasoning = f'Nova ({nova_model}) intelligently selected: {selected_model} for {task_type} task'
                    return [selected_model], reasoning
                else:
                    # Nova picked something weird, use our rules instead
                    print(f"Nova selected invalid model: {selected_model}, falling back to rule-based")
                    return self.select_models(task_type, mode, available_models_list)
            else:
                # Something went wrong, use rule-based selection
                return self.select_models(task_type, mode, available_models_list)
        except requests.exceptions.Timeout:
            print(f"Nova model selection timeout - falling back to rule-based selection")
            # Fallback to rule-based selection
            return self.select_models(task_type, mode, available_models_list)
        except Exception as e:
            print(f"Nova model selection error: {e}")
            # Fallback to rule-based selection
            return self.select_models(task_type, mode, available_models_list)
    
    def select_models(self, task_type, mode, available_models_list=None):
        """Pick which models to use based on the task and mode - OPTIMIZED for speed"""
        if available_models_list is None:
            available_models_list = self.available_models
        
        # Get the models we think are best for this task
        if task_type not in ROUTING_CONFIG:
            task_type = 'general'
        
        recommended = ROUTING_CONFIG[task_type]['models']
        reasoning = ROUTING_CONFIG[task_type]['reasoning']
        
        # Only pick from models that are actually installed
        available_recommended = [m for m in recommended if m in available_models_list]
        
        # For simple tasks, prioritize smallest/fastest models
        if task_type == 'simple' and available_recommended:
            # Prefer smaller models for speed: 3b, 4b, then 7b, then larger
            small_models = [m for m in available_recommended if '3b' in m or '4b' in m]
            if small_models:
                available_recommended = small_models + [m for m in available_recommended if m not in small_models]
        
        # Let the user know if they're missing good models
        missing_models = [m for m in recommended if m not in available_models_list]
        if missing_models:
            print(f"⚠️ Missing recommended models: {missing_models}")
            for missing_model in missing_models:
                model_info = next((m for m in AVAILABLE_MODELS if m['name'] == missing_model), None)
                if model_info:
                    print(f"  - {missing_model}: {model_info.get('description', '')}")
                    print(f"    Install with: ollama pull {missing_model}")
        
        # If we don't have any of the recommended models, just use what we have
        if not available_recommended:
            available_recommended = available_models_list[:3] if len(available_models_list) >= 3 else available_models_list
            print(f"No recommended models available, using: {available_recommended}")
        
        if not available_models_list:
            raise ValueError("No models available. Please pull at least one model using 'ollama pull <model-name>'")
        
        # How many models to use depends on the mode
        mode_config = MODE_CONFIG[mode]
        num_models = mode_config['num_models']
        
        if num_models == 'all':
            selected = available_models_list
            print(f"Expert mode: Using all {len(selected)} models")
            return selected, reasoning
        elif num_models == 1:
            # For quick mode, prioritize fastest (smallest) models
            if mode == 'quick':
                # Sort models by size: prefer 3b, 4b, then 7b, then larger
                def get_model_size_priority(model_name):
                    """Return priority number - lower is faster/smaller"""
                    name_lower = model_name.lower()
                    if '3b' in name_lower:
                        return 1
                    elif '4b' in name_lower:
                        return 2
                    elif '7b' in name_lower or '8b' in name_lower:
                        return 3
                    elif '13b' in name_lower or '12b' in name_lower:
                        return 4
                    elif '20b' in name_lower:
                        return 5
                    else:
                        return 6  # Unknown size, assume larger
                
                # Get all available models and sort by size
                all_models = available_recommended if available_recommended else available_models_list
                sorted_models = sorted(all_models, key=get_model_size_priority)
                model_to_use = sorted_models[0] if sorted_models else available_models_list[0]
                print(f"⚡ Quick mode: Selected fastest model: {model_to_use}")
            else:
                # For other modes, use recommended models
                if available_recommended:
                    qwen_models = [m for m in available_recommended if 'qwen' in m.lower()]
                    if qwen_models:
                        model_to_use = qwen_models[0]
                    else:
                        model_to_use = available_recommended[0]
                else:
                    # No recommended models, pick from what we have
                    qwen_models = [m for m in available_models_list if 'qwen' in m.lower()]
                    if qwen_models:
                        model_to_use = qwen_models[0]
                    else:
                        model_to_use = available_models_list[0]
            
            print(f"Selected model: {model_to_use} (from {len(available_recommended)} recommended, {len(available_models_list)} total available)")
            print(f"Available recommended models: {available_recommended}")
            return [model_to_use], reasoning
        else:
            # Need multiple models - pick the top ones
            selected = available_recommended[:num_models]
            # If we need more, grab from the full list
            if len(selected) < num_models and len(available_models_list) > len(selected):
                remaining = [m for m in available_models_list if m not in selected]
                selected.extend(remaining[:num_models - len(selected)])
            print(f"Selected {len(selected)} models: {selected}")
            return selected[:num_models], reasoning
    
    def analyze_query(self, message, has_images=False):
        """Figure out what kind of task this is and which models would work best"""
        task_type, task_reasoning = self.detect_task_type_with_ai(message, has_images)
        reasoning = ROUTING_CONFIG.get(task_type, ROUTING_CONFIG['general'])['reasoning']
        
        return {
            'task_type': task_type,
            'reasoning': f"{task_reasoning}. {reasoning}",
            'recommended_models': ROUTING_CONFIG.get(task_type, ROUTING_CONFIG['general'])['models']
        }

# Create the router instance
router = ModelRouter()

def query_single_model_stream(model, prompt, images=None, timeout=120):
    """Ask a model and get the response as it types"""
    try:
        payload = {
            'model': model,
            'prompt': prompt,
            'stream': True
        }
        
        if images:
            payload['images'] = images
        
        response = requests.post(
            f'{OLLAMA_API_URL}/api/generate',
            json=payload,
            timeout=timeout,
            stream=True
        )
        
        if response.status_code == 200:
            full_response = ""
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line.decode('utf-8'))
                        if 'response' in data:
                            chunk = data['response']
                            full_response += chunk
                            yield {
                                'type': 'chunk',
                                'model': model,
                                'content': chunk,
                                'done': data.get('done', False)
                            }
                        if data.get('done', False):
                            yield {
                                'type': 'complete',
                                'model': model,
                                'response': full_response,
                                'success': True
                            }
                            break
                    except json.JSONDecodeError:
                        continue
        else:
            yield {
                'type': 'error',
                'model': model,
                'error': f'HTTP {response.status_code}: {response.text[:200]}',
                'success': False
            }
    except requests.exceptions.Timeout:
        yield {
            'type': 'error',
            'model': model,
            'error': 'Request timed out',
            'success': False
        }
    except Exception as e:
        yield {
            'type': 'error',
            'model': model,
            'error': str(e),
            'success': False
        }

def query_single_model(model, prompt, images=None, timeout=120):
    """Ask a model and wait for the full response"""
    try:
        payload = {
            'model': model,
            'prompt': prompt,
            'stream': False
        }
        
        if images:
            payload['images'] = images
        
        response = requests.post(
            f'{OLLAMA_API_URL}/api/generate',
            json=payload,
            timeout=timeout
        )
        
        if response.status_code == 200:
            result = response.json()
            return {
                'success': True,
                'model': model,
                'response': result.get('response', ''),
                'error': None
            }
        else:
            return {
                'success': False,
                'model': model,
                'response': None,
                'error': f'HTTP {response.status_code}: {response.text[:200]}'
            }
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'model': model,
            'response': None,
            'error': 'Request timed out'
        }
    except Exception as e:
        return {
            'success': False,
            'model': model,
            'response': None,
            'error': str(e)
        }

def query_multiple_models(models, prompt, images=None, timeout=180):
    """Ask multiple models at the same time"""
    results = []
    
    def query_model(model):
        return query_single_model(model, prompt, images, timeout)
    
    # Run all the queries at once
    with ThreadPoolExecutor(max_workers=min(len(models), 5)) as executor:
        future_to_model = {executor.submit(query_model, model): model for model in models}
        
        for future in as_completed(future_to_model):
            result = future.result()
            results.append(result)
    
    return results

def synthesize_responses(results, task_type='general'):
    """Combine answers from multiple models into one response"""
    successful_results = [r for r in results if r['success']]
    
    if not successful_results:
        return {
            'synthesized': None,
            'individual_responses': results,
            'consensus': False,
            'error': 'All models failed to respond'
        }
    
    responses = [r['response'] for r in successful_results]
    models = [r['model'] for r in successful_results]
    
    # How we combine responses depends on what kind of task it is
    if task_type == 'simple':
        # Simple questions - just use the first good answer
        synthesized = responses[0] if responses else None
        consensus = len(set(responses)) == 1 if len(responses) > 1 else True
    
    elif task_type == 'math':
        # Math problems - check if everyone got the same answer
        synthesized = responses[0] if responses else None
        consensus = len(set(responses)) == 1 if len(responses) > 1 else False
    
    elif task_type == 'complex':
        # Complex stuff - show all the different perspectives
        synthesized = "\n\n--- Combined Analysis ---\n\n"
        for i, (model, response) in enumerate(zip(models, responses), 1):
            synthesized += f"**{model}:**\n{response}\n\n"
        consensus = False
    
    elif task_type == 'creative':
        # Creative tasks - show the main answer plus alternatives
        synthesized = responses[0] if responses else None
        if len(responses) > 1:
            synthesized += "\n\n--- Alternative Perspectives ---\n\n"
            for model, response in zip(models[1:], responses[1:]):
                synthesized += f"**{model}:**\n{response}\n\n"
        consensus = False
    
    else:
        # General questions - combine everything
        synthesized = "\n\n--- Multi-Model Analysis ---\n\n"
        for i, (model, response) in enumerate(zip(models, responses), 1):
            synthesized += f"**Model {i} ({model}):**\n{response}\n\n{'='*50}\n\n"
        consensus = len(set(responses)) == 1 if len(responses) > 1 else True
    
    return {
        'synthesized': synthesized,
        'individual_responses': successful_results,
        'consensus': consensus,
        'num_models': len(successful_results)
    }

# How Nova remembers things
class MemorySystem:
    """Keeps track of recent conversations and things Nova learns over time"""
    
    def __init__(self):
        self.short_term_memory = {}
        self.long_term_memory_file = 'nova_memory.json'
        self.load_long_term_memory()
    
    def load_long_term_memory(self):
        """Load Nova's long-term memory from the file"""
        try:
            if os.path.exists(self.long_term_memory_file):
                with open(self.long_term_memory_file, 'r', encoding='utf-8') as f:
                    self.long_term_memory = json.load(f)
            else:
                self.long_term_memory = {
                    'user_preferences': {},
                    'learned_facts': [],
                    'conversation_patterns': []
                }
        except Exception as e:
            print(f"Error loading long-term memory: {e}")
            self.long_term_memory = {
                'user_preferences': {},
                'learned_facts': [],
                'conversation_patterns': []
            }
    
    def save_long_term_memory(self):
        """Save what Nova learned to the file"""
        try:
            with open(self.long_term_memory_file, 'w', encoding='utf-8') as f:
                json.dump(self.long_term_memory, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving long-term memory: {e}")
    
    def get_conversation_context(self, chat_id, max_messages=10):
        """Get the recent messages from this conversation"""
        if chat_id not in self.short_term_memory:
            return []
        return self.short_term_memory[chat_id][-max_messages:]
    
    def add_to_conversation(self, chat_id, role, content):
        """Remember a message from this conversation"""
        # Make sure we're storing text, not bytes
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='ignore')
        elif not isinstance(content, str):
            content = str(content) if content else ''
        
        if chat_id not in self.short_term_memory:
            self.short_term_memory[chat_id] = []
        self.short_term_memory[chat_id].append({
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        })
        # Don't keep too many messages - just the last 50
        if len(self.short_term_memory[chat_id]) > 50:
            self.short_term_memory[chat_id] = self.short_term_memory[chat_id][-50:]
    
    def get_long_term_context(self):
        """Get things Nova learned that might be relevant - includes location, preferences, facts"""
        context_parts = []
        
        if self.long_term_memory.get('user_preferences'):
            prefs = self.long_term_memory['user_preferences']
            if prefs:
                context_parts.append("User Information:")
                for key, value in prefs.items():
                    if key == 'location':
                        context_parts.append(f"- Location: User lives in {value}")
                    else:
                        context_parts.append(f"- {key}: {value}")
        
        if self.long_term_memory.get('learned_facts'):
            facts = self.long_term_memory['learned_facts'][-10:]  # Last 10 facts
            if facts:
                context_parts.append("\nPersonal Context:")
                for fact in facts:
                    context_parts.append(f"- {fact}")
        
        return "\n".join(context_parts) if context_parts else None
    
    def learn_from_conversation(self, user_message, assistant_response):
        """Extract and store learnings from conversation - extracts personal info, preferences, facts"""
        # Ensure messages are strings (not bytes)
        if isinstance(user_message, bytes):
            user_message = user_message.decode('utf-8', errors='ignore')
        elif not isinstance(user_message, str):
            user_message = str(user_message) if user_message else ''
        
        if isinstance(assistant_response, bytes):
            assistant_response = assistant_response.decode('utf-8', errors='ignore')
        elif not isinstance(assistant_response, str):
            assistant_response = str(assistant_response) if assistant_response else ''
        
        user_lower = user_message.lower()
        learned_something = False
        
        # Extract location information
        location_patterns = [
            r'i\s+live\s+in\s+([a-z\s]+)',
            r'i\'m\s+from\s+([a-z\s]+)',
            r'i\'m\s+in\s+([a-z\s]+)',
            r'i\s+am\s+from\s+([a-z\s]+)',
            r'i\s+am\s+in\s+([a-z\s]+)',
            r'located\s+in\s+([a-z\s]+)',
            r'based\s+in\s+([a-z\s]+)'
        ]
        
        import re
        location = None
        for pattern in location_patterns:
            match = re.search(pattern, user_lower)
            if match:
                location = match.group(1).strip()
                # Filter out common false positives
                if location and len(location) > 2 and location not in ['the', 'a', 'an', 'this', 'that']:
                    if 'user_preferences' not in self.long_term_memory:
                        self.long_term_memory['user_preferences'] = {}
                    self.long_term_memory['user_preferences']['location'] = location.title()
                    learned_something = True
                    print(f"📍 Learned user location: {location.title()}")
                    break
        
        # Extract preferences (like, prefer, favorite)
        if 'prefer' in user_lower or 'like' in user_lower or 'favorite' in user_lower or 'favourite' in user_lower:
            if 'user_preferences' not in self.long_term_memory:
                self.long_term_memory['user_preferences'] = {}
            # Store the preference context (can be enhanced with NLP)
            learned_something = True
        
        # Extract personal facts (I am X, I work as X, I'm a X)
        fact_patterns = [
            r'i\s+am\s+(?:a\s+)?([a-z\s]+?)(?:\.|,|$)',
            r'i\'m\s+(?:a\s+)?([a-z\s]+?)(?:\.|,|$)',
            r'i\s+work\s+as\s+([a-z\s]+?)(?:\.|,|$)',
            r'i\s+work\s+in\s+([a-z\s]+?)(?:\.|,|$)',
        ]
        
        for pattern in fact_patterns:
            match = re.search(pattern, user_lower)
            if match:
                fact_text = match.group(1).strip()
                if fact_text and len(fact_text) > 2:
                    # Avoid storing generic words
                    if fact_text not in ['the', 'a', 'an', 'this', 'that', 'here', 'there']:
                        if 'learned_facts' not in self.long_term_memory:
                            self.long_term_memory['learned_facts'] = []
                        fact_entry = f"User: {user_message}"
                        if fact_entry not in self.long_term_memory['learned_facts']:
                            self.long_term_memory['learned_facts'].append(fact_entry)
                            learned_something = True
                            print(f"🧠 Learned fact: {fact_text}")
                        break
        
        # Save if we learned something new
        if learned_something:
            self.save_long_term_memory()

# Start up Nova's memory system
memory_system = MemorySystem()

# Handle chat sessions
def init_chat_session():
    """Set up the single chat session if needed - optimized"""
    if 'current_chat' not in session:
        session['current_chat'] = {
            'id': 'main_chat',
            'messages': []
        }
    if 'preferred_mode' not in session:
        session['preferred_mode'] = 'balanced'
    # Don't mark as modified unless we actually changed something

def save_session_force():
    """Force save session to disk - works even during streaming"""
    try:
        from flask import has_request_context
        import pickle
        import os
        
        # Mark as modified
        session.modified = True
        
        # During streaming, manually save session to disk
        if has_request_context():
            try:
                interface = app.session_interface
                
                # Get session ID
                session_id = session.sid if hasattr(session, 'sid') else None
                if not session_id:
                    # Generate session ID if not exists
                    session_id = interface.generate_sid()
                    session.sid = session_id
                
                # Ensure session ID is string
                if isinstance(session_id, bytes):
                    session_id = session_id.decode('utf-8')
                elif not isinstance(session_id, str):
                    session_id = str(session_id)
                    session.sid = session_id
                
                # Get session file path
                session_dir = app.config['SESSION_FILE_DIR']
                key_prefix = app.config.get('SESSION_KEY_PREFIX', 'session:')
                session_file = os.path.join(session_dir, f"{key_prefix}{session_id}")
                
                # Serialize session data
                session_data = dict(session)
                
                # Write session to file
                with open(session_file, 'wb') as f:
                    pickle.dump(session_data, f)
                
                # Set file permissions
                os.chmod(session_file, app.config.get('SESSION_FILE_MODE', 0o600))
                
                return True
            except Exception as save_error:
                print(f"⚠️ Could not save session during streaming: {save_error}")
                import traceback
                traceback.print_exc()
                # Still mark as modified so Flask saves it later
                return True
        else:
            # No request context - just mark as modified
            return True
    except Exception as e:
        print(f"❌ Error saving session: {e}")
        import traceback
        traceback.print_exc()
        return False

# Removed get_chat_by_id - using single chat now

def save_message_to_chat(chat_id, message_data):
    """Save a message to nova_memory.json conversation_patterns"""
    try:
        role = message_data.get('role', 'unknown')
        content = message_data.get('content', '')
        content_len = len(content) if content else 0
        print(f"💾 Saving {role} message to nova_memory.json: {content_len} chars")
        
        # Ensure content is a string
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='ignore')
        elif not isinstance(content, str):
            content = str(content) if content else ''
        
        # Load existing memory
        if os.path.exists('nova_memory.json'):
            with open('nova_memory.json', 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
        else:
            memory_data = {
                'user_preferences': {},
                'learned_facts': [],
                'conversation_patterns': []
            }
        
        # Initialize conversation_patterns if needed
        if 'conversation_patterns' not in memory_data:
            memory_data['conversation_patterns'] = []
        
        # Handle documents/images - use them as content if text is empty
        if not content or content.strip() == '':
            if 'documents' in message_data and message_data['documents']:
                # Use document content as message
                doc_texts = []
                for doc in message_data['documents']:
                    if isinstance(doc, bytes):
                        doc_texts.append(doc.decode('utf-8', errors='ignore')[:200])
                    elif isinstance(doc, str):
                        doc_texts.append(doc[:200])
                    else:
                        doc_texts.append(str(doc)[:200])
                content = f"[Document: {', '.join(doc_texts)}...]"
            elif 'images' in message_data and message_data['images']:
                content = f"[Image uploaded: {len(message_data['images'])} image(s)]"
        
        # Skip saving if content is still empty
        if not content or content.strip() == '':
            print(f"⚠️ Skipping {role} message with empty content")
            return True
        
        # For user messages, start a new conversation pattern
        # For assistant messages, complete the last conversation pattern
        if role == 'user':
            # Add new conversation pattern with user message
            memory_data['conversation_patterns'].append({
                'user': content,
                'assistant': '',  # Will be filled when assistant responds
                'timestamp': datetime.now().isoformat()
            })
        elif role == 'assistant':
            # Complete the last conversation pattern with assistant response
            if memory_data['conversation_patterns']:
                last_pattern = memory_data['conversation_patterns'][-1]
                # Only update if assistant field is empty (not already filled)
                if not last_pattern.get('assistant') or last_pattern.get('assistant', '').strip() == '':
                    last_pattern['assistant'] = content
                    last_pattern['timestamp'] = datetime.now().isoformat()
                else:
                    # Assistant already filled, create new pattern (shouldn't happen normally)
                    print(f"⚠️ Last pattern already has assistant response, creating new pattern")
                    memory_data['conversation_patterns'].append({
                        'user': '',
                        'assistant': content,
                        'timestamp': datetime.now().isoformat()
                    })
            else:
                # No user message yet, create new pattern
                memory_data['conversation_patterns'].append({
                    'user': '',
                    'assistant': content,
                    'timestamp': datetime.now().isoformat()
                })
        
        # Save to file
        with open('nova_memory.json', 'w', encoding='utf-8') as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Saved {role} message to nova_memory.json. Total patterns: {len(memory_data['conversation_patterns'])}")
        
        # Also save to session for current session use
        init_chat_session()
        if 'current_chat' not in session:
            session['current_chat'] = {
                'id': 'main_chat',
                'messages': []
            }
        
        current_chat = session.get('current_chat', {})
        current_messages = list(current_chat.get('messages', []))
        current_messages.append(message_data)
        
        session['current_chat'] = {
            'id': 'main_chat',
            'messages': current_messages
        }
        session.modified = True
        
        return True
        
    except Exception as e:
        print(f"❌ Error saving message to nova_memory.json: {e}")
        import traceback
        traceback.print_exc()
        return False

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        username = data.get('username')
        password = data.get('password')
        
        if check_credentials(username, password):
            session['user'] = username
            init_chat_session()
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Invalid credentials'}), 401
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

def get_model_details(model_name):
    """Get more info about a specific model"""
    try:
        response = requests.get(f'{OLLAMA_API_URL}/api/show', 
                               json={'name': model_name}, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Error getting model details for {model_name}: {e}")
        return None

def get_installed_models_with_details():
    """Get all installed models with their details"""
    try:
        response = requests.get(f'{OLLAMA_API_URL}/api/tags', timeout=5)
        if response.status_code == 200:
            models = response.json().get('models', [])
            models_with_details = []
            for model in models:
                model_name = model.get('name', '')
                model_info = {
                    'name': model_name,
                    'size': model.get('size', 0),
                    'modified_at': model.get('modified_at', ''),
                    'digest': model.get('digest', ''),
                    'family': model.get('family', ''),
                    'format': model.get('format', ''),
                    'parameter_size': model.get('parameter_size', ''),
                    'details': None
                }
                # Try to get extra info about this model
                details = get_model_details(model_name)
                if details:
                    model_info['details'] = {
                        'modelfile': details.get('modelfile', ''),
                        'parameters': details.get('parameters', ''),
                        'template': details.get('template', ''),
                        'system': details.get('system', ''),
                        'license': details.get('license', '')
                    }
                models_with_details.append(model_info)
            return models_with_details
        return []
    except Exception as e:
        print(f"Error getting installed models: {e}")
        return []

def get_model_recommendations(ram_gb=None, vram_gb=None, cpu_cores=None):
    """Get model recommendations based on system configuration"""
    recommendations = {
        'low_end': {
            'description': 'Low-end systems (4-8GB RAM, integrated graphics)',
            'models': ['gemma3:4b', 'llama3.2:3b'],
            'reasoning': 'Small quantized models (3-4B) are ideal for systems with limited resources. Gemma 3 and Llama 3.2 3B are efficient.'
        },
        'mid_range': {
            'description': 'Mid-range systems (8-16GB RAM, 4-8GB VRAM)',
            'models': ['qwen3:8b', 'llama3.2:8b', 'llava:7b'],
            'reasoning': '8B quantized models provide best balance of performance and quality. Qwen3-8B excels at general tasks, LLaVA-7B for vision.'
        },
        'high_end': {
            'description': 'High-end systems (16GB+ RAM, 8GB+ VRAM)',
            'models': ['gpt-oss:20b', 'deepseek-v3.2', 'llava:13b', 'pixtral:12b'],
            'reasoning': 'Larger quantized models (12-20B) offer best quality. GPT-OSS-20B for reasoning, DeepSeek-V3.2 for text understanding, Pixtral-12B for vision.'
        },
        'vision': {
            'description': 'For image understanding tasks',
            'models': ['llava:7b', 'llava:13b', 'qwen2-vl', 'pixtral:12b', 'llama3.2-vision:11b'],
            'reasoning': 'Multimodal models are required for image analysis. LLaVA-7B for balance, LLaVA-13B for quality, Pixtral-12B for strong understanding, Qwen2VL for lightweight needs.'
        }
    }
    
    # Determine system tier
    if ram_gb and vram_gb:
        if ram_gb >= 16 and vram_gb >= 8:
            tier = 'high_end'
        elif ram_gb >= 8 and vram_gb >= 4:
            tier = 'mid_range'
        else:
            tier = 'low_end'
    else:
        tier = 'mid_range'  # Default assumption
    
    return {
        'system_tier': tier,
        'recommendations': recommendations,
        'recommended_for_your_system': recommendations[tier]
    }

@app.route('/api/models')
def get_models():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    installed = get_cached_models()
    return jsonify({
        'installed': installed,
        'recommended': RECOMMENDED_MODELS,
        'available_models': AVAILABLE_MODELS
    })


@app.route('/api/models/refresh', methods=['POST'])
def refresh_models():
    """Force refresh the model cache"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    global _models_cache_time
    _models_cache_time = 0  # Expire cache
    models = get_cached_models()
    router.load_available_models()
    return jsonify({'success': True, 'count': len(models)})

@app.route('/api/models/recommendations', methods=['POST'])
def get_model_recommendations_endpoint():
    """Suggest which models would work best for the user's system"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        data = request.get_json() or {}
        ram_gb = data.get('ram_gb')
        vram_gb = data.get('vram_gb')
        cpu_cores = data.get('cpu_cores')
        
        recommendations = get_model_recommendations(ram_gb, vram_gb, cpu_cores)
        return jsonify(recommendations)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle when users upload images or documents"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        filename = secure_filename(file.filename)
        file_type = request.form.get('type', 'image')
        
        if file_type == 'image':
            if not allowed_file(filename, 'image'):
                return jsonify({'error': 'Invalid image format. Supported: PNG, JPG, JPEG, GIF, WEBP, BMP'}), 400
            
            file.seek(0)
            img_base64 = process_image(file)
            
            return jsonify({
                'success': True,
                'type': 'image',
                'filename': filename,
                'data': img_base64,
                'mime_type': mimetypes.guess_type(filename)[0] or 'image/jpeg'
            })
        
        elif file_type == 'document':
            if not allowed_file(filename, 'document'):
                return jsonify({'error': 'Invalid document format. Supported: PDF, DOCX, DOC, TXT, MD'}), 400
            
            file.seek(0)
            text_content = process_document(file, filename)
            
            return jsonify({
                'success': True,
                'type': 'document',
                'filename': filename,
                'content': text_content,
                'size': len(text_content)
            })
        
        else:
            return jsonify({'error': 'Invalid file type'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/router/analyze', methods=['POST'])
def analyze_query():
    """Figure out which model would be best without actually running it"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    message = data.get('message', '')
    images = data.get('images', [])
    
    # Ensure message is a string (not bytes)
    if isinstance(message, bytes):
        message = message.decode('utf-8', errors='ignore')
    elif not isinstance(message, str):
        message = str(message) if message else ''
    
    if not message and not images:
        return jsonify({'error': 'Message or image is required'}), 400
    
    router.load_available_models()
    analysis = router.analyze_query(message, has_images=len(images) > 0)
    
    return jsonify(analysis)

def enhance_prompt_with_ai(user_prompt):
    """Use a small model to improve the user's prompt before sending to the main model"""
    try:
        enhancement_model = 'gemma3:4b'
        
        enhancement_prompt = f"""You are a prompt enhancement assistant. Your task is to improve the user's prompt to make it clearer, more specific, and more likely to get a good response from an AI assistant.

User's original prompt:
{user_prompt}

Enhance this prompt by:
1. Clarifying any ambiguous parts
2. Adding necessary context
3. Making it more specific if it's too vague
4. Keeping the original intent and meaning

Return ONLY the enhanced prompt, nothing else. Do not add explanations or meta-commentary."""

        payload = {
            'model': enhancement_model,
            'prompt': enhancement_prompt,
            'stream': False,
            'options': {
                'temperature': 0.7,
                'num_predict': 200
            }
        }
        
        response = requests.post(
            f'{OLLAMA_API_URL}/api/generate',
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            enhanced = result.get('response', '').strip()
            enhanced = enhanced.strip('"').strip("'").strip('```').strip()
            return enhanced if enhanced else user_prompt
        else:
            return user_prompt
    except Exception as e:
        print(f"Prompt enhancement failed: {e}")
        return user_prompt

@app.route('/api/chat', methods=['POST'])
def chat():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    message = data.get('message', '')
    manual_model = data.get('model')
    mode = data.get('mode', session.get('preferred_mode', 'balanced'))
    auto_select = data.get('auto_select', True)
    enhance_prompt = data.get('enhance_prompt', False)
    chat_id = 'main_chat'  # Always use single main chat
    images = data.get('images', [])
    documents = data.get('documents', [])
    
    # Make sure the message is text
    if isinstance(message, bytes):
        message = message.decode('utf-8', errors='ignore')
    elif not isinstance(message, str):
        message = str(message) if message else ''
    
    # Optionally improve the prompt before sending
    # SKIP for simple messages to avoid delay
    original_message = message
    enhanced_prompt_used = False
    message_lower = message.lower().strip()
    is_simple = message_lower in ['hi', 'hello', 'hey'] or len(message.strip()) < 15
    
    if enhance_prompt and message and not images and not documents and not is_simple:
        try:
            enhanced_message = enhance_prompt_with_ai(message)
            if enhanced_message and enhanced_message != message and len(enhanced_message.strip()) > 0:
                message = enhanced_message
                enhanced_prompt_used = True
                print(f"Original: {original_message[:50]}...")
                print(f"Enhanced: {message[:50]}...")
        except Exception as e:
            print(f"Prompt enhancement error: {e}")
    
    if not message and not images and not documents:
        return jsonify({'error': 'Message, image, or document is required'}), 400
    
    init_chat_session()
    session['preferred_mode'] = mode
    
    # Build the prompt with all the context Nova needs
    router.load_available_models()
    installed_models_list = router.available_models
    installed_models_str = ', '.join(installed_models_list) if installed_models_list else 'None (please install models)'
    
    # List what models are currently installed
    model_info_lines = []
    if installed_models_list:
        model_info_lines.append(f"\nCurrently installed models ({len(installed_models_list)}):")
        for model_name in installed_models_list:
            model_info = next((m for m in AVAILABLE_MODELS if m['name'] == model_name), None)
            if model_info:
                model_info_lines.append(f"  - {model_name}: {model_info.get('description', '')} ({model_info.get('size', 'Unknown size')})")
            else:
                model_info_lines.append(f"  - {model_name}")
    
    model_info_text = '\n'.join(model_info_lines) if model_info_lines else "\nNo models currently installed. User should install models using 'ollama pull <model-name>'"
    
    system_prompt = f"""You are Nova, an intelligent AI assistant powered by multiple local language models. 
Nova automatically selects the best model for each task to provide optimal responses.
You have access to both short-term memory (conversation history) and long-term memory (learned preferences and facts).
IMPORTANT: Use the user's personal information (like location, preferences, facts) from long-term memory context when relevant.
For example, if the user mentioned they live in Bangalore, you can reference this naturally in conversations.
Be helpful, accurate, and concise. When discussing code, provide well-formatted code blocks with proper syntax highlighting.

IMPORTANT: When users ask about available models, installed models, or model recommendations:
1. Always check and inform them about the currently installed models listed below
2. Provide accurate information about model sizes, capabilities, and use cases
3. If they ask about system requirements or recommendations, guide them based on their system specs
4. Suggest installing additional models if they have limited options

Nova's Router Model: Nova uses {NOVA_ROUTER_MODEL} (with {NOVA_ROUTER_BACKUP} and {NOVA_ROUTER_FALLBACK} as backups) for intelligent model selection and routing decisions. This allows Nova to analyze user requests and automatically select the best model for each task.

{model_info_text}

Available model options (curated quantized models):

🧠 Text-Only Models:
- qwen3:8b: Best balance of quality & CPU performance (~5GB)
- gpt-oss:20b: Strong reasoning & chat quality (~12GB)
- deepseek-v3.2: Excellent text understanding (~8GB)
- glm-4.6: Strong performance & licensing (~9GB)
- gemma3:4b: Efficient, fast (~2.5GB)
- gemma3:8b: Efficient, fast (~5GB)
- llama3.2:3b: Meta's strong text model (~2GB)
- llama3.2:8b: Meta's strong text model (~5GB)

📸 Multimodal (Text + Image) Models:
- llava:7b: Great offline vision + text chatbot (~4GB)
- llava:13b: Improved but heavier than 7B (~7GB)
- qwen2-vl: Lightweight multimodal model (~5GB)
- pixtral:12b: Strong vision understanding (~7GB)
- llama3.2-vision:11b: Balanced multimodal option (~6.5GB)

Model recommendations by system:
- Low-end (4-8GB RAM): gemma3:4b, llama3.2:3b
- Mid-range (8-16GB RAM, 4-8GB VRAM): qwen3:8b, llama3.2:8b, llava:7b
- High-end (16GB+ RAM, 8GB+ VRAM): gpt-oss:20b, deepseek-v3.2, llava:13b, pixtral:12b

When users ask "what models do you have" or similar questions, always list the currently installed models first, then provide helpful recommendations."""
    
    full_prompt = message
    
    # Add things Nova learned over time
    long_term_context = memory_system.get_long_term_context()
    if long_term_context:
        full_prompt = f"--- Long-term Memory Context ---\n{long_term_context}\n\n--- Current Question ---\n{full_prompt}"
    
    # Build conversation history from nova_memory.json so model has full context
    # (Same source as UI - ensures follow-ups like "write it more clearly" work)
    conversation_history = None
    try:
        if os.path.exists('nova_memory.json'):
            with open('nova_memory.json', 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
                patterns = memory_data.get('conversation_patterns', [])
                if patterns:
                    context_messages = []
                    for p in patterns:
                        user_content = p.get('user', '').strip()
                        assistant_content = p.get('assistant', '').strip()
                        
                        # Skip patterns where both are empty
                        if not user_content and not assistant_content:
                            continue
                        
                        if isinstance(user_content, bytes):
                            user_content = user_content.decode('utf-8', errors='ignore')
                        if isinstance(assistant_content, bytes):
                            assistant_content = assistant_content.decode('utf-8', errors='ignore')
                        
                        if user_content:
                            context_messages.append(f"User: {user_content}")
                        if assistant_content:
                            context_messages.append(f"Nova: {assistant_content}")
                    if context_messages:
                        conversation_history = "\n".join(context_messages)
                        print(f"📚 Sending conversation history from nova_memory.json: {len(patterns)} turns ({len(conversation_history)} chars)")
    except Exception as e:
        print(f"⚠️ Error loading conversation history for prompt: {e}")
    
    # Fallback: use session if nova_memory.json had no history
    if not conversation_history and 'current_chat' in session:
        init_chat_session()
        current_chat = session.get('current_chat', {})
        messages = current_chat.get('messages', [])
        if messages:
            context_messages = []
            for msg in messages:
                role_label = "User" if msg.get('role') == 'user' else "Nova"
                content = msg.get('content', '')
                if isinstance(content, bytes):
                    content = content.decode('utf-8', errors='ignore')
                elif not isinstance(content, str):
                    content = str(content) if content else ''
                if content:
                    context_messages.append(f"{role_label}: {content}")
            if context_messages:
                conversation_history = "\n".join(context_messages)
                print(f"📚 Sending conversation history from session: {len(messages)} messages")
    
    # Add any documents the user uploaded
    if documents:
        doc_texts = []
        for doc in documents:
            if isinstance(doc, bytes):
                doc_texts.append(doc.decode('utf-8', errors='ignore'))
            elif not isinstance(doc, str):
                doc_texts.append(str(doc))
            else:
                doc_texts.append(doc)
        doc_text = "\n\n--- Document Content ---\n\n".join(doc_texts)
        full_prompt = f"{doc_text}\n\n--- User Question ---\n{message}" if message else doc_text
    
    # Put it all together: system + conversation history + current question
    if conversation_history:
        full_prompt = (
            f"{system_prompt}\n\n"
            "--- Conversation History (use this context for follow-up questions like 'write it more clearly', 'expand that', 'summarize') ---\n"
            f"{conversation_history}\n\n"
            "--- Current Question ---\n"
            f"{full_prompt}"
        )
    else:
        full_prompt = f"{system_prompt}\n\n--- Current Question ---\n{full_prompt}"
    
    # Use single chat - initialize if needed
    init_chat_session()
    
    # Always use the single main chat
    chat_id = 'main_chat'
    
    # Get current_chat from session
    if 'current_chat' not in session:
        session['current_chat'] = {
            'id': 'main_chat',
            'messages': []
        }
        session.modified = True
        save_session_force()
    
    # Get current_chat and ensure it's properly structured
    current_chat = session.get('current_chat', {})
    
    # Ensure messages list exists
    if 'messages' not in current_chat:
        current_chat['messages'] = []
    
    # CRITICAL: Always update session with a new dict to ensure Flask detects changes
    # Flask sessions don't always detect changes to nested dicts, so create a new one
    session['current_chat'] = {
        'id': current_chat.get('id', 'main_chat'),
        'messages': list(current_chat.get('messages', []))  # Create new list
    }
    session.modified = True
    
    # Save the user's message FIRST, before generating response
    user_message_data = {
        'role': 'user',
        'content': original_message if enhanced_prompt_used else message,
        'timestamp': datetime.now().isoformat(),
        'mode': mode,
        'enhanced': enhanced_prompt_used,
        'enhanced_content': message if enhanced_prompt_used else None
    }
    
    if images:
        user_message_data['images'] = images
    if documents:
        user_message_data['documents'] = documents
    
    # CRITICAL: Save user message immediately
    save_success = save_message_to_chat(chat_id, user_message_data)
    if not save_success:
        print(f"⚠️ WARNING: Failed to save user message")
    
    memory_system.add_to_conversation('main_chat', 'user', message)
    
    try:
        available_models = router.available_models
        
        print(f"\n=== Model Selection Debug ===")
        print(f"Available models from Ollama: {available_models}")
        print(f"Number of models: {len(available_models)}")
        
        if not available_models:
            return jsonify({
                'error': 'No models available. Please ensure Ollama is running and you have pulled at least one model. Example: ollama pull qwen3:8b'
            }), 400
        
        # Figure out which model(s) to use
        if not auto_select and manual_model:
            is_available, error_msg = router.check_model_availability(manual_model, available_models)
            if not is_available:
                return jsonify({
                    'error': error_msg,
                    'model': manual_model,
                    'install_command': f'ollama pull {manual_model}'
                }), 404
            
            selected_models = [manual_model]
            routing_reasoning = f'Manually selected: {manual_model}'
            task_type = 'manual'
            print(f"Manual selection: {manual_model}")
        else:
            # Let Nova pick the best model - OPTIMIZED for speed
            # For simple/short messages, use fast keyword detection instead of AI
            message_lower = message.lower().strip()
            is_simple_greeting = message_lower in ['hi', 'hello', 'hey', 'hi there', 'hello there', 'hey there'] or len(message.strip()) < 20
            
            if is_simple_greeting and mode in ['quick', 'balanced']:
                # Fast path: Use keyword-based detection for simple messages
                task_type = 'simple'
                task_reasoning = "Simple greeting detected - using fast routing"
                selected_models, routing_reasoning = router.select_models(task_type, mode, available_models)
                routing_reasoning = f"{task_reasoning}. {routing_reasoning}"
                print(f"⚡ Fast routing: {task_type} -> {selected_models}")
            else:
                # Full AI routing for complex messages
                try:
                    task_type, task_reasoning = router.detect_task_type_with_ai(message, has_images=len(images) > 0)
                    print(f"Nova detected task type: {task_type}")
                    print(f"Task detection reasoning: {task_reasoning}")
                    
                    if mode == 'balanced' or mode == 'quick':
                        selected_models, routing_reasoning = router.select_models_with_ai(
                            task_type, mode, available_models, message
                        )
                        routing_reasoning = f"{task_reasoning}. {routing_reasoning}"
                    else:
                        selected_models, routing_reasoning = router.select_models(task_type, mode, available_models)
                        routing_reasoning = f"{task_reasoning}. {routing_reasoning}"
                    
                    print(f"Selected models: {selected_models}")
                    print(f"Routing reasoning: {routing_reasoning}")
                except ValueError as e:
                    return jsonify({'error': str(e)}), 400
            
            # Check if selected models are available (for both fast and AI routing paths)
            missing_models = [m for m in selected_models if m not in available_models]
            if missing_models:
                missing_model = missing_models[0]
                is_available, error_msg = router.check_model_availability(missing_model, available_models)
                return jsonify({
                    'error': error_msg,
                    'model': missing_model,
                    'install_command': f'ollama pull {missing_model}',
                    'recommended_models': [m for m in ROUTING_CONFIG.get(task_type, {}).get('models', []) if m in available_models]
                }), 404
        
        # If there are images, we need a vision model
        if images:
            vision_models = [m for m in selected_models if is_vision_model(m)]
            if not vision_models:
                available_vision_models = [m for m in available_models if is_vision_model(m)]
                if available_vision_models:
                    error_msg = f'Selected models do not support images.\n\n'
                    error_msg += f'Available vision models:\n'
                    for vm in available_vision_models:
                        model_info = next((m for m in AVAILABLE_MODELS if m['name'] == vm), None)
                        if model_info:
                            error_msg += f'  - {vm}: {model_info.get("description", "")}\n'
                    error_msg += f'\nTo use vision capabilities, enable auto-select or manually select a vision model.'
                else:
                    error_msg = f'No vision models are installed. Selected models do not support images.\n\n'
                    error_msg += f'To install vision models, run:\n'
                    error_msg += f'  ollama pull llava:7b\n'
                    error_msg += f'  ollama pull llava:13b\n'
                    error_msg += f'  ollama pull qwen2-vl\n'
                    error_msg += f'  ollama pull pixtral:12b\n'
                    error_msg += f'  ollama pull llama3.2-vision:11b'
                
                return jsonify({
                    'error': error_msg,
                    'available_vision_models': available_vision_models,
                    'install_commands': [
                        'ollama pull llava:7b',
                        'ollama pull llava:13b',
                        'ollama pull qwen2-vl',
                        'ollama pull pixtral:12b',
                        'ollama pull llama3.2-vision:11b'
                    ]
                }), 400
            selected_models = vision_models
        
        # Ask the model(s) for a response
        mode_config = MODE_CONFIG[mode]
        use_streaming = data.get('stream', True)
        
        ai_response = ""
        models_used = []
        individual_responses = []
        
        if mode_config['strategy'] == 'single' or len(selected_models) == 1:
            if use_streaming:
                def generate():
                    full_response = ""
                    model_name = selected_models[0]
                    
                    # Send start event with chat_id immediately
                    yield f"data: {json.dumps({'type': 'start', 'model': model_name, 'chat_id': 'main_chat', 'routing_reasoning': routing_reasoning if auto_select else None, 'task_type': task_type if auto_select else None})}\n\n"
                    
                    # For quick mode, add thinking delay (2-3 seconds)
                    if mode == 'quick':
                        import time
                        thinking_time = 2.5  # seconds
                        print(f"⚡ Quick mode: Thinking for {thinking_time} seconds...")
                        # Send thinking updates during delay
                        for i in range(int(thinking_time)):
                            yield f"data: {json.dumps({'type': 'thinking', 'message': f'Processing your request... ({i+1}/{int(thinking_time)})'})}\n\n"
                            time.sleep(1)
                        # Final thinking message
                        yield f"data: {json.dumps({'type': 'thinking', 'message': 'Ready to respond...'})}\n\n"
                        time.sleep(thinking_time - int(thinking_time))  # Remaining fraction
                    
                    for chunk_data in query_single_model_stream(model_name, full_prompt, images if images else None, timeout=180):
                        if chunk_data['type'] == 'chunk':
                            full_response += chunk_data['content']
                            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk_data['content'], 'model': model_name})}\n\n"
                        elif chunk_data['type'] == 'complete':
                            assistant_message = {
                                'role': 'assistant',
                                'content': full_response,
                                'timestamp': datetime.now().isoformat(),
                                'models': [model_name],
                                'mode': mode,
                                'routing_reasoning': routing_reasoning if auto_select else None,
                                'task_type': task_type if auto_select else None
                            }
                            
                            # Save assistant message - CRITICAL for persistence
                            save_success = save_message_to_chat('main_chat', assistant_message)
                            if save_success:
                                print(f"✅ Saved assistant message ({len(full_response)} chars) to session")
                            else:
                                print(f"❌ FAILED to save assistant message to session!")
                            
                            memory_system.add_to_conversation('main_chat', 'assistant', full_response)
                            memory_system.learn_from_conversation(message, full_response)
                            
                            yield f"data: {json.dumps({'type': 'complete', 'chat_id': 'main_chat', 'model': model_name})}\n\n"
                        elif chunk_data['type'] == 'error':
                            yield f"data: {json.dumps({'type': 'error', 'error': chunk_data['error']})}\n\n"
                            break
                
                return Response(stream_with_context(generate()), mimetype='text/event-stream')
            else:
                result = query_single_model(selected_models[0], full_prompt, images if images else None, timeout=180)
                
                if result['success']:
                    ai_response = result['response']
                    models_used = [result['model']]
                else:
                    if 'install_command' in result:
                        return jsonify({
                            'error': result['error'],
                            'model': result['model'],
                            'install_command': result['install_command']
                        }), 404
                    return jsonify({'error': f"Model {result['model']} failed: {result['error']}"}), 500
        
        else:
            # Multiple models working together
            results = query_multiple_models(selected_models, full_prompt, images if images else None, timeout=180)
            synthesis = synthesize_responses(results, task_type)
            
            if synthesis['synthesized']:
                ai_response = synthesis['synthesized']
                models_used = [r['model'] for r in synthesis['individual_responses']]
                individual_responses = synthesis['individual_responses']
            else:
                return jsonify({'error': synthesis.get('error', 'All models failed')}), 500
        
        # Save the response if we're not streaming
        if not use_streaming or (mode_config['strategy'] != 'single' and len(selected_models) > 1):
            assistant_message = {
                'role': 'assistant',
                'content': ai_response,
                'timestamp': datetime.now().isoformat(),
                'models': models_used,
                'mode': mode,
                'routing_reasoning': routing_reasoning if auto_select else None,
                'task_type': task_type if auto_select else None
            }
            
            if mode in ['deep', 'expert'] and individual_responses and len(individual_responses) > 1:
                assistant_message['individual_responses'] = individual_responses
            
            save_message_to_chat('main_chat', assistant_message)
            memory_system.add_to_conversation('main_chat', 'assistant', ai_response)
            memory_system.learn_from_conversation(message, ai_response)
            
            return jsonify({
                'response': ai_response,
                'chat_id': 'main_chat',
                'models': models_used,
                'mode': mode,
                'routing_reasoning': routing_reasoning if auto_select else None,
                'task_type': task_type if auto_select else None,
                'individual_responses': individual_responses if (mode in ['deep', 'expert'] and individual_responses) else None
            })
            
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Request timed out. The model might be too large or slow.'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat')
def get_current_chat():
    """Get chat history from nova_memory.json"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Load messages from nova_memory.json
    messages = []
    try:
        if os.path.exists('nova_memory.json'):
            with open('nova_memory.json', 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
                conversation_patterns = memory_data.get('conversation_patterns', [])
                
                # Convert conversation_patterns to message format
                # Filter out patterns with empty user AND assistant (invalid patterns)
                for pattern in conversation_patterns:
                    user_content = pattern.get('user', '').strip()
                    assistant_content = pattern.get('assistant', '').strip()
                    
                    # Skip patterns where both are empty
                    if not user_content and not assistant_content:
                        continue
                    
                    # Add user message if it exists
                    if user_content:
                        messages.append({
                            'role': 'user',
                            'content': user_content,
                            'timestamp': pattern.get('timestamp', '')
                        })
                    
                    # Add assistant message if it exists
                    if assistant_content:
                        messages.append({
                            'role': 'assistant',
                            'content': assistant_content,
                            'timestamp': pattern.get('timestamp', '')
                        })
    except Exception as e:
        print(f"⚠️ Error loading chat from nova_memory.json: {e}")
        messages = []
    
    # Debug: Log message count and types
    user_count = sum(1 for msg in messages if msg.get('role') == 'user')
    assistant_count = sum(1 for msg in messages if msg.get('role') == 'assistant')
    print(f"📥 Loading chat from nova_memory.json: {len(messages)} total messages ({user_count} user, {assistant_count} assistant)")
    
    # Fast response - only process content if bytes (most are already strings)
    chat_response = {
        'id': 'main_chat',
        'messages': []
    }
    
    # Fast message copying - minimal conversion, preserve full content
    for msg in messages:
        msg_copy = dict(msg)
        # Only decode bytes (rare case)
        content = msg_copy.get('content', '')
        if isinstance(content, bytes):
            msg_copy['content'] = content.decode('utf-8', errors='ignore')
        
        # Debug: Log assistant messages
        if msg_copy.get('role') == 'assistant':
            content_len = len(content) if content else 0
            content_preview = content[:100] if content else 'NO CONTENT'
            print(f"  📤 Sending assistant message: {content_len} chars - {content_preview}...")
        
        # Ensure full content is preserved (no truncation)
        chat_response['messages'].append(msg_copy)
    
    return jsonify(chat_response)

@app.route('/api/chat/refresh', methods=['POST'])
def refresh_chat():
    """Clear chat history by clearing nova_memory.json conversation_patterns"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Load existing memory
        if os.path.exists('nova_memory.json'):
            with open('nova_memory.json', 'r', encoding='utf-8') as f:
                memory_data = json.load(f)
        else:
            memory_data = {
                'user_preferences': {},
                'learned_facts': [],
                'conversation_patterns': []
            }
        
        # Clear conversation patterns (chat history) but keep preferences and facts
        memory_data['conversation_patterns'] = []
        
        # Save back to file
        with open('nova_memory.json', 'w', encoding='utf-8') as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)
        
        # Also clear session chat
        init_chat_session()
        session['current_chat'] = {
            'id': 'main_chat',
            'messages': []
        }
        session.modified = True
        save_session_force()
        
        print("✅ Chat refreshed - nova_memory.json conversation_patterns cleared")
        
        return jsonify({'success': True, 'message': 'Chat history cleared successfully'})
    except Exception as e:
        print(f"❌ Error clearing chat: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to clear chat: {str(e)}'}), 500

# Removed chat history endpoints - using single chat now

if __name__ == '__main__':
    os.makedirs('flask_session', exist_ok=True)
    # Pre-load models at startup so first page load is instant
    print("🚀 Pre-loading model cache...")
    get_cached_models()
    router.load_available_models()
    print("✅ Ready")
    debug = os.environ.get('NOVA_DEBUG', '0') == '1'
    host = os.environ.get('NOVA_HOST', '127.0.0.1')
    port = int(os.environ.get('NOVA_PORT', '5000'))
    app.run(debug=debug, host=host, port=port)
