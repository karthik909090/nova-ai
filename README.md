# 🌟 Nova — Local AI Chat with Intelligent Model Routing

> **Run multiple local LLMs through one sleek interface. Nova automatically picks the best model for each task — no cloud, no API keys, no data leaving your machine.**

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?logo=flask)
![Ollama](https://img.shields.io/badge/Ollama-required-orange)
![License](https://img.shields.io/badge/License-MIT-green)

---

## ✨ What is Nova?

Nova is a self-hosted AI chat application that connects to [Ollama](https://ollama.ai) and intelligently routes your questions to the best available local model. Instead of picking a model yourself, Nova analyzes your message and selects the right tool automatically — coding questions go to coding models, creative writing goes to creative models, and so on.

**Everything runs 100% locally. Your conversations never leave your machine.**

---

## 🚀 Features

- **🧠 Smart Model Router** — Nova uses a small fast model (Gemma 3 4B) to analyze your request and pick the optimal model from your installed collection
- **⚡ 4 Response Modes**
  - `Quick` — Fastest response with the smallest available model
  - `Balanced` — AI-routed to the best model for the task *(recommended)*
  - `Deep` — 3 models in parallel for diverse perspectives
  - `Expert` — All models vote on the best answer
- **📸 Vision Support** — Upload images and chat with multimodal models (LLaVA, Pixtral, Qwen2-VL, Llama 3.2 Vision)
- **📄 Document Upload** — Attach PDFs, DOCX, TXT, and Markdown files for analysis
- **🗂️ Long-Term Memory** — Nova learns your preferences and personal context across sessions
- **💬 Streaming Responses** — Real-time token-by-token streaming just like ChatGPT
- **🔐 Session Auth** — Simple login system to protect your local instance
- **🌙 Dark Mode UI** — Clean, responsive chat interface inspired by modern AI assistants

---

## 🧠 How Model Routing Works

```
User Message → Nova Router (Gemma 3 4B) → Task Classification
                                              │
              ┌───────────────────────────────┼──────────────────────────┐
              │               │               │              │            │
           coding           math          creative        complex      general
              │               │               │              │            │
    qwen2.5-coder      qwen2.5:14b      llama3.1:8b    gpt-oss:20b   qwen2.5:7b
    deepseek-coder      gpt-oss:20b     qwen2.5:7b     qwen2.5:14b   qwen2.5:14b
```

Nova falls back to keyword detection if the router model is unavailable or too slow.

---

## 📋 Requirements

- Python 3.10+
- [Ollama](https://ollama.ai) running locally on `http://localhost:11434`
- At least one model pulled via `ollama pull <model>`

### Recommended Models to Get Started

```bash
# Fast & general purpose
ollama pull qwen2.5:7b-instruct

# Coding
ollama pull qwen2.5-coder:7b

# Vision (images)
ollama pull llava:7b

# Nova's router (required for AI routing)
ollama pull gemma3:4b
```

---

## 🛠️ Installation

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/nova.git
cd nova

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install flask flask-session requests Pillow PyPDF2 python-docx

# 4. Make sure Ollama is running
ollama serve

# 5. Run Nova
python app.py
```

Open **http://localhost:5000** and log in with:
- **Username:** `admin`
- **Password:** `admin123`

> ⚠️ Change the credentials in `app.py` before exposing to a network.

---

## 📦 Project Structure

```
nova/
├── app.py              # Main Flask application & routing logic
├── static/
│   ├── css/styles.css  # Dark-mode UI styles
│   └── js/app.js       # Frontend chat logic (streaming, file upload)
├── templates/
│   ├── index.html      # Main chat interface
│   └── login.html      # Login page
├── flask_session/      # Server-side session storage
└── nova_memory.json    # Long-term memory (auto-created)
```

---

## ⚙️ Configuration

Edit `app.py` to customize:

| Setting | Default | Description |
|---|---|---|
| `USER_CREDENTIALS` | `admin:admin123` | Login credentials |
| `OLLAMA_API_URL` | `http://localhost:11434` | Ollama endpoint |
| `NOVA_ROUTER_MODEL` | `gemma3:4b` | Model used for task routing |
| `MODELS_CACHE_TTL` | `60` | Seconds before model list refreshes |

---

## 🗺️ Roadmap

- [ ] Multi-user support with individual memory
- [ ] RAG (Retrieval-Augmented Generation) with local vector DB
- [ ] Model performance analytics dashboard
- [ ] REST API for external integrations
- [ ] Docker Compose setup
- [ ] Conversation export (JSON / Markdown)

---

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first.

1. Fork the repo
2. Create your feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m 'Add my feature'`
4. Push and open a PR

---

## 📄 License

MIT — do whatever you want, just keep the attribution.

---

*Built with Flask + Ollama. No cloud. No tracking. Just local AI.*
