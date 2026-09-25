# 🌟 Nova — Local AI Chat with Intelligent Model Routing

> **Run multiple local LLMs through one interface. Nova picks a suitable model for each task — no cloud, no API keys, no data leaving your machine.**

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?logo=flask)
![Ollama](https://img.shields.io/badge/Ollama-required-orange)
![License](https://img.shields.io/badge/License-MIT-green)

---

## ✨ What is Nova?

Nova is a self-hosted chat application that connects to [Ollama](https://ollama.com) and routes each message to a suitable local model. Instead of choosing a model yourself, Nova classifies your request with a small, fast router model and sends it to the best match from the models you have installed: coding questions go to coding models, creative writing to general-purpose models, and so on.

**Everything runs locally. Your conversations never leave your machine.**

---

## 🚀 Features

- **🧠 Model router** — a small model (Gemma 3 4B by default) classifies each request and selects a model from your installed collection. If the router is unavailable or too slow, Nova falls back to keyword detection.
- **⚡ Four response modes**
  - `Quick` — one fast model, lowest latency
  - `Balanced` — one model chosen by the router *(recommended)*
  - `Deep` — three models answer in parallel; their responses are combined for comparison
  - `Expert` — every installed model answers in parallel; responses are shown side by side, with a simple check for whether they agree exactly
- **📸 Vision support** — upload images and chat with multimodal models (LLaVA, Pixtral, Qwen2-VL, Llama 3.2 Vision)
- **📄 Document upload** — attach PDF, DOCX, TXT and Markdown files for analysis
- **🗂️ Long-term memory** — Nova stores simple preferences and facts you share across sessions, in a local `nova_memory.json` file
- **💬 Streaming responses** — token-by-token output
- **🔐 Login** — credentials set through environment variables, compared in constant time

---

## 🧠 How model routing works

```
User message → Router (Gemma 3 4B) → Task classification
                                            │
          ┌──────────────┬──────────────┬───┴──────────┬──────────────┐
       coding          math         creative        complex        general
          │              │              │              │              │
  qwen2.5-coder     qwen2.5:14b    llama3.1:8b    gpt-oss:20b    qwen2.5:7b
  deepseek-coder    gpt-oss:20b    qwen2.5:7b     qwen2.5:14b    qwen2.5:14b
```

Only models you have pulled are considered, so routing adapts to your installation.

---

## 📋 Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally (default `http://localhost:11434`)
- At least one model pulled with `ollama pull <model>`

Suggested starting set:

```bash
ollama pull gemma3:4b             # router (needed for AI routing)
ollama pull qwen2.5:7b-instruct   # general purpose
ollama pull qwen2.5-coder:7b      # coding
ollama pull llava:7b              # images
```

---

## 🛠️ Installation

```bash
# 1. Clone the repository
git clone https://github.com/karthik909090/nova-ai.git
cd nova-ai

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your settings file and set a password
cp .env.example .env             # Windows: copy .env.example .env
#    then edit .env and set NOVA_PASSWORD

# 5. Make sure Ollama is running, then start Nova
ollama serve
python app.py
```

Open **http://localhost:5000** and log in with the username and password from your `.env` file. If you didn't set `NOVA_PASSWORD`, Nova generates a random password and prints it in the terminal at startup.

---

## ⚙️ Configuration

All settings live in `.env` (see `.env.example`). The `.env` file is ignored by git.

| Variable | Default | Description |
|---|---|---|
| `NOVA_USERNAME` | `admin` | Login username |
| `NOVA_PASSWORD` | *(random, printed at startup)* | Login password |
| `NOVA_SECRET_KEY` | *(random per run)* | Signs sessions. Set it to stay logged in across restarts |
| `OLLAMA_API_URL` | `http://localhost:11434` | Ollama endpoint |
| `NOVA_HOST` | `127.0.0.1` | Interface to listen on |
| `NOVA_PORT` | `5000` | Port |
| `NOVA_DEBUG` | `0` | Flask debug mode (`1` to enable) |

The router model (`NOVA_ROUTER_MODEL`) and model-cache refresh time (`MODELS_CACHE_TTL`) can be changed in `app.py`.

---

## 🔒 Security notes

- Nova listens on **localhost only** by default. To reach it from other machines, set `NOVA_HOST=0.0.0.0` deliberately and use a strong password.
- **Never enable `NOVA_DEBUG` on a network-reachable host.** Flask's debugger allows code execution.
- `nova_memory.json`, `uploads/` and `flask_session/` can contain personal information from your chats. They are git-ignored; keep them that way.
- Nova is designed for a single user on a trusted machine. It is not hardened for public internet deployment.

---

## 📦 Project structure

```
nova-ai/
├── app.py              # Flask application, router and model orchestration
├── static/
│   ├── css/styles.css  # Dark-mode UI styles
│   └── js/app.js       # Frontend chat logic (streaming, file upload)
├── templates/
│   ├── index.html      # Main chat interface
│   └── login.html      # Login page
├── .env.example        # Settings template (copy to .env)
├── requirements.txt
└── LICENSE
```

Created at runtime (git-ignored): `flask_session/`, `uploads/`, `nova_memory.json`.

---

## 🗺️ Roadmap

- [ ] Real answer aggregation for Expert mode (judge model or majority voting)
- [ ] Evaluate routing accuracy against a labelled set of prompts
- [ ] Multi-user support with separate memory
- [ ] Retrieval-augmented generation with a local vector database
- [ ] Docker Compose setup
- [ ] Conversation export (JSON / Markdown)

---

## 👤 Author

**Karthik Shivakumar** — [github.com/karthik909090](https://github.com/karthik909090)

Issues and pull requests are welcome.

## 📄 License

MIT — see [LICENSE](LICENSE).
