class OllamaChatApp {
    constructor() {
        this.currentChatId = null;
        this.currentModel = '';  // FIX: Start empty, will be set after models load
        this.currentMode = 'balanced';
        this.autoSelect = true;  // FIX: Match the HTML checkbox `checked` attribute
        this.isLoading = false;
        this.attachedFiles = []; // Store attached files: {type, filename, data, content}
        this.abortController = null; // For canceling requests
        this.thinkingStatus = null; // Current thinking status
        this.userScrolledUp = false; // Track if user manually scrolled up
        this.autoScrollEnabled = true; // Whether auto-scroll is enabled
        this.enhancePrompt = false; // Enable prompt enhancement
        this.isScrollingProgrammatically = false; // Flag to prevent scroll handler interference
        this.isUserScrolling = false; // Track if user is actively scrolling
        this.isAnalyzing = false; // Prevent multiple analyze calls
        // Throttle timers for smooth streaming
        this.updateThrottle = null;
        this.highlightThrottle = null;
        this.scrollThrottle = null;
        
        this.init();
    }
    
    async init() {
        this.cacheElements();
        this.attachEventListeners();
        
        // FIX: Sync autoSelect state from checkbox BEFORE any other UI setup
        this.autoSelect = this.autoSelectToggle.checked;
        this.updateModelSelectorState();
        
        // Load chat first (fast) then models (can be slower)
        await this.loadCurrentChat();
        
        // Load models in parallel/background (non-blocking)
        this.loadModels().catch(err => {
            console.error('Error loading models:', err);
        });
        
        this.setupTextareaAutoResize();
        this.updateModeTooltip();
    }
    
    cacheElements() {
        this.chatMessages = document.getElementById('chatMessages');
        this.messageInput = document.getElementById('messageInput');
        this.sendBtn = document.getElementById('sendBtn');
        this.stopBtn = document.getElementById('stopBtn');
        this.modelSelect = document.getElementById('modelSelect');
        this.modeSelect = document.getElementById('modeSelect');
        this.autoSelectToggle = document.getElementById('autoSelectToggle');
        this.enhancePromptToggle = document.getElementById('enhancePromptToggle');
        this.modelSelector = document.getElementById('modelSelector');
        this.modeTooltip = document.getElementById('modeTooltip');
        this.refreshChatBtn = document.getElementById('refreshChatBtn');
        this.logoutBtn = document.getElementById('logoutBtn');
        this.refreshModels = document.getElementById('refreshModels');
        this.imageUpload = document.getElementById('imageUpload');
        this.documentUpload = document.getElementById('documentUpload');
        this.filePreviewContainer = document.getElementById('filePreviewContainer');
        this.filePreviewItems = document.getElementById('filePreviewItems');
        this.clearFilesBtn = document.getElementById('clearFilesBtn');
    }
    
    attachEventListeners() {
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        // FIX: Update currentModel immediately and reliably on dropdown change
        this.modelSelect.addEventListener('change', (e) => {
            const selectedValue = e.target.value;
            if (selectedValue && selectedValue !== '') {
                this.currentModel = selectedValue;
                console.log(`✅ Model manually selected: ${this.currentModel}`);
                // FIX: When user picks a model, turn off auto-select automatically
                if (this.autoSelectToggle.checked) {
                    this.autoSelectToggle.checked = false;
                    this.autoSelect = false;
                    this.updateModelSelectorState();
                    console.log('🔄 Auto-select disabled because user manually selected a model');
                }
            }
        });
        
        this.modeSelect.addEventListener('change', (e) => {
            this.currentMode = e.target.value;
            this.updateModeTooltip();
            this.updateModelSelectorState();
        });
        
        // FIX: Sync this.autoSelect with checkbox state reliably
        this.autoSelectToggle.addEventListener('change', (e) => {
            this.autoSelect = e.target.checked;
            console.log(`🔄 Auto-select toggled: ${this.autoSelect}`);
            this.updateModelSelectorState();
        });
        
        this.enhancePromptToggle.addEventListener('change', (e) => {
            this.enhancePrompt = e.target.checked;
        });
        
        this.refreshChatBtn.addEventListener('click', () => this.refreshChat());
        this.logoutBtn.addEventListener('click', () => this.logout());
        this.refreshModels.addEventListener('click', async () => {
            // Bust server cache first, then reload
            await fetch('/api/models/refresh', { method: 'POST' });
            this.loadModels();
        });
        this.stopBtn.addEventListener('click', () => this.stopGeneration());
        
        // File upload listeners
        this.imageUpload.addEventListener('change', (e) => this.handleImageUpload(e));
        this.documentUpload.addEventListener('change', (e) => this.handleDocumentUpload(e));
        this.clearFilesBtn.addEventListener('click', () => this.clearAllFiles());
        
        // Track user scrolling to prevent auto-scroll interference
        this.chatMessages.addEventListener('scroll', () => this.handleScroll(), { passive: true });
        
        this.lastScrollTop = 0;
        this.isUserScrolling = false;
    }
    
    handleScroll() {
        if (this.isScrollingProgrammatically) {
            return;
        }
        
        const chatContainer = this.chatMessages;
        const scrollTop = chatContainer.scrollTop;
        const scrollHeight = chatContainer.scrollHeight;
        const clientHeight = chatContainer.clientHeight;
        const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
        
        const scrollDelta = Math.abs(scrollTop - this.lastScrollTop);
        const isManualScroll = scrollDelta > 10;
        
        if (isManualScroll && distanceFromBottom > 150) {
            this.userScrolledUp = true;
            this.autoScrollEnabled = false;
            this.isUserScrolling = true;
        } else if (distanceFromBottom <= 100) {
            this.userScrolledUp = false;
            this.autoScrollEnabled = true;
            this.isUserScrolling = false;
        }
        
        this.lastScrollTop = scrollTop;
    }
    
    updateModeTooltip() {
        const modeDescriptions = {
            'quick': 'Fastest response with single best model',
            'balanced': 'Smart router selects optimal model based on task',
            'deep': 'Multiple models provide diverse perspectives',
            'expert': 'All available models vote on the answer'
        };
        this.modeTooltip.textContent = modeDescriptions[this.currentMode] || '';
    }
    
    updateModelSelectorState() {
        if (this.autoSelect) {
            // FIX: Only visually disable — do NOT clear the value or wipe currentModel
            this.modelSelect.disabled = true;
            this.modelSelector.classList.add('auto-mode');
        } else {
            // Enable the select so user can pick
            this.modelSelect.disabled = false;
            this.modelSelector.classList.remove('auto-mode');
            
            // FIX: If no model is currently selected, pick the first installed one
            if (!this.currentModel || !this.modelSelect.value) {
                this.selectFirstInstalledModel();
            } else {
                // Restore the dropdown to show the currentModel
                this.modelSelect.value = this.currentModel;
            }
        }
    }
    
    // FIX: New helper — selects the first option from the "Installed Models" optgroup
    selectFirstInstalledModel() {
        const installedGroup = this.modelSelect.querySelector('optgroup');
        if (installedGroup && installedGroup.options && installedGroup.options.length > 0) {
            const firstOption = installedGroup.options[0];
            if (firstOption.value) {
                this.modelSelect.value = firstOption.value;
                this.currentModel = firstOption.value;
                console.log(`🎯 Auto-selected first installed model: ${this.currentModel}`);
                return;
            }
        }
        // Fallback: iterate all options and find first non-empty
        for (let i = 0; i < this.modelSelect.options.length; i++) {
            const opt = this.modelSelect.options[i];
            if (opt.value && opt.value !== '') {
                this.modelSelect.value = opt.value;
                this.currentModel = opt.value;
                console.log(`🎯 Auto-selected model (fallback): ${this.currentModel}`);
                return;
            }
        }
    }
    
    debounceAnalyzeQuery() {
        return;
    }
    
    async analyzeQuery() {
        if (this.isAnalyzing) return;
        
        const message = this.messageInput.value.trim();
        if (!message || this.isLoading) return;
        
        this.isAnalyzing = true;
        try {
            const response = await fetch('/api/router/analyze', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({message, images: this.attachedFiles.filter(f => f.type === 'image').map(f => f.data)})
            });
            
            const data = await response.json();
            if (data.task_type) {
                console.log('Task type:', data.task_type, 'Reasoning:', data.reasoning);
            }
        } catch (error) {
            // Silent fail
        } finally {
            this.isAnalyzing = false;
        }
    }
    
    setupTextareaAutoResize() {
        this.messageInput.addEventListener('input', () => {
            this.messageInput.style.height = 'auto';
            this.messageInput.style.height = this.messageInput.scrollHeight + 'px';
        });
    }
    
    async loadModels() {
        this.modelSelect.innerHTML = '';
        const loadingOpt = document.createElement('option');
        loadingOpt.value = '';
        loadingOpt.textContent = 'Fetching models...';
        this.modelSelect.appendChild(loadingOpt);

        try {
            const response = await fetch('/api/models');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            
            this.modelSelect.innerHTML = '';
            const installedModelNames = [];
            
            // Add installed models
            if (data.installed && data.installed.length > 0) {
                const installedGroup = document.createElement('optgroup');
                installedGroup.label = `✅ Installed Models (${data.installed.length})`;
                
                data.installed.forEach(model => {
                    const option = document.createElement('option');
                    // FIX: Always extract .name robustly
                    const modelName = typeof model === 'string' ? model : (model.name || String(model));
                    const size = typeof model === 'object' && model != null ? (model.size || 0) : 0;
                    option.value = modelName;
                    option.textContent = size > 0 ? `${modelName} (${this.formatSize(size)})` : modelName;
                    installedGroup.appendChild(option);
                    installedModelNames.push(modelName);
                });
                
                this.modelSelect.appendChild(installedGroup);
            }
            
            // Add recommended models (not installed yet)
            if (data.recommended && data.recommended.length > 0) {
                // FIX: Filter out models that are already installed to avoid confusion
                const notInstalled = data.recommended.filter(m => {
                    const name = typeof m === 'string' ? m : (m.name || '');
                    return !installedModelNames.includes(name);
                });
                
                if (notInstalled.length > 0) {
                    const recommendedGroup = document.createElement('optgroup');
                    recommendedGroup.label = '📦 Recommended (run: ollama pull <name>)';
                    
                    notInstalled.forEach(model => {
                        const option = document.createElement('option');
                        const name = typeof model === 'string' ? model : (model.name || String(model));
                        option.value = name;
                        // FIX: Mark uninstalled models clearly so users don't accidentally select them
                        option.textContent = typeof model === 'object' && model.description
                            ? `${name} — ${model.description}`
                            : name;
                        option.disabled = true; // FIX: Prevent selecting uninstalled models
                        option.style.color = '#888';
                        recommendedGroup.appendChild(option);
                    });
                    
                    this.modelSelect.appendChild(recommendedGroup);
                }
            }

            // Auto-pick best default model from installed list
            const preferredDefaults = [
                'qwen2.5:7b-instruct',
                'qwen2.5:7b',
                'llama3.1:8b',
                'qwen2.5:3b',
                'gemma3:4b'
            ];

            if (installedModelNames.length > 0) {
                let defaultModel = null;
                for (const preferred of preferredDefaults) {
                    if (installedModelNames.includes(preferred)) {
                        defaultModel = preferred;
                        break;
                    }
                }
                if (!defaultModel) defaultModel = installedModelNames[0];
                
                this.modelSelect.value = defaultModel;
                this.currentModel = defaultModel;
                console.log(`✅ Default model set to: ${defaultModel}`);
            }

            this.updateModelSelectorState();
            
        } catch (error) {
            console.error('Error loading models:', error);
            this.modelSelect.innerHTML = '';
            this.showError('Failed to load models. Make sure Ollama is running.');
        }
    }
    
    formatSize(bytes) {
        const mb = bytes / (1024 * 1024);
        const gb = mb / 1024;
        if (gb >= 1) {
            return `${gb.toFixed(1)}GB`;
        } else {
            return `${mb.toFixed(0)}MB`;
        }
    }
    
    async loadCurrentChat() {
        try {
            const startTime = performance.now();
            
            const response = await fetch('/api/chat', {
                method: 'GET',
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                },
                credentials: 'same-origin'
            });
            
            if (!response.ok) {
                throw new Error(`Failed to load chat: ${response.status}`);
            }
            
            const chat = await response.json();
            const loadTime = performance.now() - startTime;
            
            this.currentChatId = chat.id || 'main_chat';
            
            let messages = chat.messages || [];
            
            const userCount = messages.filter(m => m && m.role === 'user').length;
            const assistantCount = messages.filter(m => m && m.role === 'assistant').length;
            console.log(`✅ Chat loaded in ${loadTime.toFixed(0)}ms: ${messages.length} total (${userCount} user, ${assistantCount} assistant)`);
            
            const validMessages = messages.filter(msg => msg && msg.role);
            
            if (validMessages.length === 0) {
                this.showWelcomeScreen();
            } else {
                this.renderMessages(validMessages);
            }
        } catch (error) {
            console.error('❌ Error loading chat:', error);
            this.showWelcomeScreen();
        }
    }
    
    async refreshChat() {
        if (!confirm('Are you sure you want to refresh the chat? This will clear all messages.')) {
            return;
        }
        
        try {
            const response = await fetch('/api/chat/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin'
            });
            
            if (!response.ok) {
                throw new Error(`Failed to refresh chat: ${response.status}`);
            }
            
            this.chatMessages.innerHTML = '';
            this.currentChatId = 'main_chat';
            this.showWelcomeScreen();
            
        } catch (error) {
            console.error('❌ Error refreshing chat:', error);
            this.showError(`Failed to refresh chat: ${error.message}`);
        }
    }
    
    stopGeneration() {
        if (this.abortController) {
            this.abortController.abort();
            this.abortController = null;
        }
        this.isLoading = false;
        this.sendBtn.disabled = false;
        this.stopBtn.style.display = 'none';
        this.sendBtn.style.display = 'flex';
        this.updateThinkingStatus('Generation stopped by user', 'error');
    }
    
    updateThinkingStatus(message, type = 'info') {
        this.thinkingStatus = { message, type };
        const thinkingIndicator = document.querySelector('.thinking-indicator');
        if (thinkingIndicator) {
            thinkingIndicator.textContent = message;
            thinkingIndicator.className = `thinking-indicator ${type}`;
        }
    }
    
    showThinkingIndicator(message) {
        const existing = document.querySelector('.thinking-indicator');
        if (existing) existing.remove();
        
        const indicator = document.createElement('div');
        indicator.className = 'thinking-indicator';
        indicator.innerHTML = `
            <div class="thinking-content">
                <div class="thinking-spinner"></div>
                <span class="thinking-text">${message}</span>
            </div>
        `;
        
        const lastMessage = this.chatMessages.lastElementChild;
        if (lastMessage && lastMessage.classList.contains('message')) {
            this.chatMessages.insertBefore(indicator, lastMessage.nextSibling);
        } else {
            this.chatMessages.appendChild(indicator);
        }
        
        this.scrollToBottom();
    }
    
    removeThinkingIndicator() {
        const indicator = document.querySelector('.thinking-indicator');
        if (indicator) indicator.remove();
    }
    
    renderMessages(messages) {
        if (!messages || messages.length === 0) {
            this.showWelcomeScreen();
            return;
        }
        
        this.chatMessages.innerHTML = '';
        
        for (const message of messages) {
            if (!message || !message.role) continue;
            try {
                this.addMessageToUI(message);
            } catch (error) {
                console.error('❌ Error rendering message:', error, message);
            }
        }
        
        requestAnimationFrame(() => {
            this.highlightCodeBlocks();
            this.scrollToBottom(true);
        });
    }
    
    addMessageToUI(message) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${message.role}`;
        
        const avatar = message.role === 'user' ? '👤' : '✨';
        
        let modelInfo = '';
        let routingInfo = '';
        
        if (message.role === 'assistant') {
            const models = message.models || (message.model ? [message.model] : []);
            if (models.length > 0) {
                const modelList = models.length === 1
                    ? models[0]
                    : `${models.length} models: ${models.join(', ')}`;
                
                modelInfo = `<div class="model-badge">
                    <span class="model-icon">✨</span>
                    <span class="model-name-text">Nova (${modelList})</span>
                    ${message.mode ? `<span class="mode-badge">${this.getModeLabel(message.mode)}</span>` : ''}
                </div>`;
                
                if (message.routing_reasoning) {
                    routingInfo = `<div class="routing-reasoning">
                        <strong>Routing:</strong> ${message.routing_reasoning}
                        ${message.task_type ? ` <span class="task-type">(${message.task_type})</span>` : ''}
                    </div>`;
                }
            }
        }
        
        let imagesHtml = '';
        if (message.images && message.images.length > 0) {
            imagesHtml = '<div class="message-images">';
            message.images.forEach(imgData => {
                imagesHtml += `<img src="data:image/jpeg;base64,${imgData}" alt="Attached image" class="message-image">`;
            });
            imagesHtml += '</div>';
        }
        
        let documentsHtml = '';
        if (message.documents && message.documents.length > 0) {
            documentsHtml = '<div class="message-documents">';
            message.documents.forEach((doc, idx) => {
                const preview = doc.substring(0, 200) + (doc.length > 200 ? '...' : '');
                documentsHtml += `<div class="message-document"><strong>Document ${idx + 1}:</strong><pre>${this.escapeHtml(preview)}</pre></div>`;
            });
            documentsHtml += '</div>';
        }
        
        let individualResponsesHtml = '';
        if (message.individual_responses && message.individual_responses.length > 1) {
            individualResponsesHtml = '<div class="individual-responses">';
            message.individual_responses.forEach((resp) => {
                if (resp.success) {
                    individualResponsesHtml += `
                        <div class="individual-response">
                            <div class="response-header" onclick="this.parentElement.classList.toggle('expanded')">
                                <span class="response-model">${resp.model}</span>
                                <span class="expand-icon">▼</span>
                            </div>
                            <div class="response-content">${this.formatMessageContent(resp.response)}</div>
                        </div>`;
                } else {
                    individualResponsesHtml += `
                        <div class="individual-response error">
                            <div class="response-header">
                                <span class="response-model">${resp.model}</span>
                                <span class="error-badge">Failed</span>
                            </div>
                            <div class="response-content error">${resp.error || 'Request failed'}</div>
                        </div>`;
                }
            });
            individualResponsesHtml += '</div>';
        }
        
        let actionButtons = '';
        if (message.role === 'user') {
            actionButtons = `
                <div class="message-actions">
                    <button class="btn-action" onclick="app.editMessage(this)" title="Edit message">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                        </svg>
                    </button>
                </div>`;
        } else if (message.role === 'assistant') {
            actionButtons = `
                <div class="message-actions">
                    <button class="btn-action" onclick="app.regenerateMessage(this)" title="Regenerate response">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="23 4 23 10 17 10"></polyline>
                            <polyline points="1 20 1 14 7 14"></polyline>
                            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                        </svg>
                    </button>
                    <button class="btn-action" onclick="app.copyMessage(this)" title="Copy message">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                    </button>
                </div>`;
        }
        
        let contentHtml = '';
        if (message.content) {
            contentHtml = this.formatMessageContent(message.content);
        } else if (message.role === 'assistant' && !imagesHtml && !documentsHtml) {
            contentHtml = '<div class="message-text"><em>[No response content]</em></div>';
        }
        
        messageDiv.innerHTML = `
            <div class="message-avatar">${avatar}</div>
            <div class="message-content-wrapper">
                <div class="message-content">
                    ${modelInfo}
                    ${routingInfo}
                    ${imagesHtml}
                    ${documentsHtml}
                    ${contentHtml}
                    ${individualResponsesHtml}
                </div>
                ${actionButtons}
            </div>
        `;
        
        this.chatMessages.appendChild(messageDiv);
        
        setTimeout(() => {
            this.highlightCodeBlocks();
        }, 100);
    }
    
    editMessage(button) {
        const messageDiv = button.closest('.message');
        const contentWrapper = messageDiv.querySelector('.message-content');
        const originalText = contentWrapper.textContent.trim();
        
        const editDiv = document.createElement('div');
        editDiv.className = 'message-edit';
        editDiv.innerHTML = `
            <textarea class="edit-textarea">${this.escapeHtml(originalText)}</textarea>
            <div class="edit-actions">
                <button class="btn-primary" onclick="app.saveEdit(this)">Save & Send</button>
                <button class="btn-secondary" onclick="app.cancelEdit(this)">Cancel</button>
            </div>
        `;
        
        messageDiv.querySelector('.message-content-wrapper').appendChild(editDiv);
        const textarea = editDiv.querySelector('.edit-textarea');
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }
    
    async saveEdit(button) {
        const editDiv = button.closest('.message-edit');
        const textarea = editDiv.querySelector('.edit-textarea');
        const newText = textarea.value.trim();
        
        if (!newText) {
            this.showError('Message cannot be empty');
            return;
        }
        
        const messageDiv = editDiv.closest('.message');
        const contentWrapper = messageDiv.querySelector('.message-content');
        
        contentWrapper.innerHTML = this.formatMessageContent(newText);
        editDiv.remove();
        
        this.messageInput.value = newText;
        this.sendMessage();
    }
    
    cancelEdit(button) {
        const editDiv = button.closest('.message-edit');
        editDiv.remove();
    }
    
    async regenerateMessage(button) {
        const messageDiv = button.closest('.message');
        
        let userMessage = messageDiv.previousElementSibling;
        while (userMessage && (!userMessage.classList.contains('message') || userMessage.querySelector('.message-avatar')?.textContent !== '👤')) {
            userMessage = userMessage.previousElementSibling;
        }
        
        if (!userMessage) {
            this.showError('Cannot regenerate: User message not found');
            return;
        }
        
        const originalText = userMessage.querySelector('.message-content').textContent.trim();
        messageDiv.remove();
        this.messageInput.value = originalText;
        this.sendMessage();
    }
    
    copyMessage(button) {
        const messageDiv = button.closest('.message');
        const contentWrapper = messageDiv.querySelector('.message-content');
        const text = contentWrapper.textContent;
        
        navigator.clipboard.writeText(text).then(() => {
            const originalHTML = button.innerHTML;
            button.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            button.style.color = 'var(--success-color)';
            setTimeout(() => {
                button.innerHTML = originalHTML;
                button.style.color = '';
            }, 2000);
        }).catch(() => {
            this.showError('Failed to copy message');
        });
    }
    
    formatMessageContent(content) {
        if (!content) return '';
        
        const codeBlocks = [];
        let codeBlockIndex = 0;
        
        const codeBlockRegex = /```(\w+)?\n?([\s\S]*?)```/g;
        let processedContent = content.replace(codeBlockRegex, (match, lang, code) => {
            const placeholder = `__CODE_BLOCK_${codeBlockIndex}__`;
            codeBlocks.push({
                language: lang || 'plaintext',
                code: code.trim()
            });
            codeBlockIndex++;
            return placeholder;
        });
        
        let html = processedContent
            .replace(/^### (.*$)/gim, '<h3>$1</h3>')
            .replace(/^## (.*$)/gim, '<h2>$1</h2>')
            .replace(/^# (.*$)/gim, '<h1>$1</h1>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)/g, '<em>$1</em>')
            .replace(/^\* (.*$)/gim, '<li>$1</li>')
            .replace(/\n/g, '<br>');
        
        codeBlockIndex = 0;
        html = html.replace(/__CODE_BLOCK_(\d+)__/g, () => {
            const block = codeBlocks[codeBlockIndex];
            const codeId = `code-block-${Date.now()}-${codeBlockIndex}`;
            codeBlockIndex++;
            
            return `
                <div class="code-block-container">
                    <div class="code-block-header">
                        <span class="code-language">${block.language}</span>
                        <button class="btn-copy-code" onclick="copyCodeBlock('${codeId}')" title="Copy code">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                            Copy
                        </button>
                    </div>
                    <pre><code id="${codeId}" class="language-${block.language}">${this.escapeHtml(block.code)}</code></pre>
                </div>
            `;
        });
        
        html = html.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
        
        return `<div class="message-text">${html}</div>`;
    }
    
    highlightCodeBlocks() {
        if (typeof hljs !== 'undefined') {
            const streamingMessage = document.querySelector('.message.streaming');
            const container = streamingMessage || this.chatMessages;
            
            container.querySelectorAll('pre code').forEach((block) => {
                if (block.classList.contains('hljs')) return;
                try {
                    block.classList.remove('hljs');
                    hljs.highlightElement(block);
                } catch (e) {
                    // Silent fail
                }
            });
        }
    }
    
    showWelcomeScreen() {
        this.chatMessages.innerHTML = `
            <div class="welcome-screen">
                <h1>Welcome to Nova ✨</h1>
                <p>Your intelligent AI assistant. Nova automatically selects the best model for each task. Upload images or documents to analyze them!</p>
                <div class="feature-cards">
                    <div class="feature-card">
                        <div class="feature-icon">💬</div>
                        <h3>Multiple Models</h3>
                        <p>Switch between different AI models</p>
                    </div>
                    <div class="feature-card">
                        <div class="feature-icon">🖼️</div>
                        <h3>Image Support</h3>
                        <p>Upload and analyze images with vision models</p>
                    </div>
                    <div class="feature-card">
                        <div class="feature-icon">📄</div>
                        <h3>Document Analysis</h3>
                        <p>Upload PDFs, DOCX, and text files</p>
                    </div>
                    <div class="feature-card">
                        <div class="feature-icon">🔒</div>
                        <h3>Private & Local</h3>
                        <p>All data stays on your machine</p>
                    </div>
                </div>
            </div>
        `;
    }
    
    async handleImageUpload(event) {
        const files = Array.from(event.target.files);
        
        for (const file of files) {
            if (file.size > 50 * 1024 * 1024) {
                this.showError(`File ${file.name} is too large. Maximum size is 50MB.`);
                continue;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('type', 'image');
            
            try {
                const response = await fetch('/api/upload', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    this.attachedFiles.push({
                        type: 'image',
                        filename: data.filename,
                        data: data.data,
                        mime_type: data.mime_type
                    });
                    this.updateFilePreview();
                } else {
                    this.showError(data.error || 'Failed to upload image');
                }
            } catch (error) {
                this.showError('Failed to upload image');
            }
        }
        
        event.target.value = '';
    }
    
    async handleDocumentUpload(event) {
        const files = Array.from(event.target.files);
        
        for (const file of files) {
            if (file.size > 50 * 1024 * 1024) {
                this.showError(`File ${file.name} is too large. Maximum size is 50MB.`);
                continue;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('type', 'document');
            
            try {
                const response = await fetch('/api/upload', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    this.attachedFiles.push({
                        type: 'document',
                        filename: data.filename,
                        content: data.content,
                        size: data.size
                    });
                    this.updateFilePreview();
                } else {
                    this.showError(data.error || 'Failed to upload document');
                }
            } catch (error) {
                this.showError('Failed to upload document');
            }
        }
        
        event.target.value = '';
    }
    
    updateFilePreview() {
        if (this.attachedFiles.length === 0) {
            this.filePreviewContainer.style.display = 'none';
            return;
        }
        
        this.filePreviewContainer.style.display = 'block';
        this.filePreviewItems.innerHTML = '';
        
        this.attachedFiles.forEach((file, index) => {
            const previewItem = document.createElement('div');
            previewItem.className = 'file-preview-item';
            
            if (file.type === 'image') {
                previewItem.innerHTML = `
                    <div class="file-preview-image">
                        <img src="data:${file.mime_type};base64,${file.data}" alt="${file.filename}">
                    </div>
                    <div class="file-preview-info">
                        <span class="file-name">${file.filename}</span>
                        <span class="file-type">Image</span>
                    </div>
                    <button class="btn-remove-file" data-index="${index}" title="Remove">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                `;
            } else {
                const previewText = file.content.substring(0, 100) + (file.content.length > 100 ? '...' : '');
                previewItem.innerHTML = `
                    <div class="file-preview-icon">📄</div>
                    <div class="file-preview-info">
                        <span class="file-name">${file.filename}</span>
                        <span class="file-type">Document (${this.formatFileSize(file.size)} characters)</span>
                        <div class="file-preview-text">${this.escapeHtml(previewText)}</div>
                    </div>
                    <button class="btn-remove-file" data-index="${index}" title="Remove">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                `;
            }
            
            const removeBtn = previewItem.querySelector('.btn-remove-file');
            removeBtn.addEventListener('click', () => this.removeFile(index));
            
            this.filePreviewItems.appendChild(previewItem);
        });
    }
    
    removeFile(index) {
        this.attachedFiles.splice(index, 1);
        this.updateFilePreview();
    }
    
    clearAllFiles() {
        this.attachedFiles = [];
        this.updateFilePreview();
    }
    
    formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    getModeLabel(mode) {
        const modeLabels = {
            'quick': '⚡ Quick',
            'balanced': '🎯 Balanced',
            'deep': '🔬 Deep',
            'expert': '👥 Expert'
        };
        return modeLabels[mode] || mode;
    }
    
    async sendMessage() {
        if (this.isLoading) return;
        
        const message = this.messageInput.value.trim();
        if (!message && this.attachedFiles.length === 0) return;
        
        // FIX: Validate we have a model when not using auto-select
        if (!this.autoSelect && !this.currentModel) {
            this.showError('Please select a model from the dropdown, or enable Auto-Select.');
            return;
        }
        
        this.messageInput.value = '';
        this.messageInput.style.height = 'auto';
        
        const welcomeScreen = this.chatMessages.querySelector('.welcome-screen');
        if (welcomeScreen) welcomeScreen.remove();
        
        const images = this.attachedFiles.filter(f => f.type === 'image').map(f => f.data);
        const documents = this.attachedFiles.filter(f => f.type === 'document').map(f => f.content);
        
        const userMessage = {
            role: 'user',
            content: message || (images.length > 0 ? '[Image attached]' : '[Document attached]'),
            images: images.length > 0 ? images : undefined,
            documents: documents.length > 0 ? documents : undefined,
            mode: this.currentMode
        };
        this.addMessageToUI(userMessage);
        
        const filesToSend = [...this.attachedFiles];
        this.clearAllFiles();
        
        this.showLoading();
        this.showThinkingIndicator(this.enhancePrompt && message ? 'Enhancing your prompt...' : 'Analyzing your message...');
        this.isLoading = true;
        this.sendBtn.disabled = true;
        this.sendBtn.style.display = 'none';
        this.stopBtn.style.display = 'flex';
        
        this.abortController = new AbortController();
        
        try {
            const requestBody = {
                message: message,
                mode: this.currentMode,
                auto_select: this.autoSelect,
                enhance_prompt: this.enhancePrompt,
                chat_id: 'main_chat',
                stream: true,
                images: images.length > 0 ? images : undefined,
                documents: documents.length > 0 ? documents : undefined
            };
            
            // FIX: Only include model when auto_select is explicitly false AND we have a model chosen
            if (!this.autoSelect && this.currentModel) {
                requestBody.model = this.currentModel;
                console.log(`📤 Sending with manual model: ${this.currentModel}`);
            } else {
                console.log(`📤 Sending with auto-select (mode: ${this.currentMode})`);
            }
            
            const useStreaming = (this.currentMode === 'quick' || this.currentMode === 'balanced');
            
            if (useStreaming) {
                await this.sendMessageStreaming(requestBody, filesToSend);
            } else {
                await this.sendMessageNonStreaming(requestBody, filesToSend);
            }
            
            this.removeThinkingIndicator();
        } catch (error) {
            this.removeThinkingIndicator();
            if (error.name === 'AbortError' || error.message.includes('aborted')) {
                this.updateThinkingStatus('Generation stopped', 'error');
                setTimeout(() => this.removeThinkingIndicator(), 2000);
            } else {
                const errorData = error.errorData || null;
                this.showError(error.message || 'Failed to send message. Make sure Ollama is running.', errorData);
            }
            this.attachedFiles = filesToSend;
            this.updateFilePreview();
        } finally {
            this.isLoading = false;
            this.sendBtn.disabled = false;
            this.sendBtn.style.display = 'flex';
            this.stopBtn.style.display = 'none';
            this.abortController = null;
        }
    }
    
    async sendMessageStreaming(requestBody, filesToSend) {
        try {
            this.removeLoading();
            
            const streamingMessageDiv = document.createElement('div');
            streamingMessageDiv.className = 'message assistant streaming';
            streamingMessageDiv.innerHTML = `
                <div class="message-avatar">✨</div>
                <div class="message-content-wrapper">
                    <div class="message-content">
                        <div class="model-badge">
                            <span class="model-icon">✨</span>
                            <span class="model-name-text">Nova is thinking...</span>
                            <span class="mode-badge">${this.getModeLabel(this.currentMode)}</span>
                        </div>
                        <div class="streaming-content"></div>
                    </div>
                </div>
            `;
            this.chatMessages.appendChild(streamingMessageDiv);
            
            const contentDiv = streamingMessageDiv.querySelector('.streaming-content');
            const modelNameSpan = streamingMessageDiv.querySelector('.model-name-text');
            let fullResponse = '';
            
            this.updateThinkingStatus('Generating response...', 'info');
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody),
                signal: this.abortController.signal
            });
            
            if (!response.ok) {
                const errorText = await response.text();
                let errorData;
                try {
                    errorData = JSON.parse(errorText);
                } catch {
                    errorData = { error: errorText || 'Request failed' };
                }
                const error = new Error(errorData.error || 'Request failed');
                error.errorData = errorData;
                throw error;
            }
            
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';
                
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            
                            if (data.type === 'start') {
                                this.currentChatId = 'main_chat';
                                modelNameSpan.textContent = data.model || 'Nova';
                                
                                if (data.routing_reasoning) {
                                    const routingDiv = document.createElement('div');
                                    routingDiv.className = 'routing-reasoning';
                                    routingDiv.innerHTML = `<strong>Routing:</strong> ${data.routing_reasoning}${data.task_type ? ` <span class="task-type">(${data.task_type})</span>` : ''}`;
                                    streamingMessageDiv.querySelector('.message-content').insertBefore(routingDiv, contentDiv.nextSibling);
                                }
                            } else if (data.type === 'thinking') {
                                if (modelNameSpan) modelNameSpan.textContent = data.message || 'Nova is thinking...';
                                this.updateThinkingStatus(data.message || 'Thinking...', 'info');
                            } else if (data.type === 'chunk') {
                                fullResponse += data.content;
                                this.removeThinkingIndicator();
                                
                                if (!this.updateThrottle) {
                                    this.updateThrottle = requestAnimationFrame(() => {
                                        contentDiv.innerHTML = this.formatMessageContent(fullResponse);
                                        this.highlightCodeBlocks();
                                        
                                        const chatContainer = this.chatMessages;
                                        const distanceFromBottom = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight;
                                        if (this.autoScrollEnabled && distanceFromBottom < 200) {
                                            chatContainer.scrollTop = chatContainer.scrollHeight;
                                        }
                                        
                                        this.updateThrottle = null;
                                    });
                                }
                            } else if (data.type === 'complete') {
                                this.currentChatId = 'main_chat';
                                
                                if (this.updateThrottle) { cancelAnimationFrame(this.updateThrottle); this.updateThrottle = null; }
                                if (this.highlightThrottle) { clearTimeout(this.highlightThrottle); this.highlightThrottle = null; }
                                if (this.scrollThrottle) { cancelAnimationFrame(this.scrollThrottle); this.scrollThrottle = null; }
                                
                                streamingMessageDiv.classList.remove('streaming');
                                contentDiv.innerHTML = this.formatMessageContent(fullResponse);
                                streamingMessageDiv.setAttribute('data-message-id', `msg_${Date.now()}`);
                                
                                const contentWrapper = streamingMessageDiv.querySelector('.message-content-wrapper');
                                if (contentWrapper && !contentWrapper.querySelector('.message-actions')) {
                                    const actionsDiv = document.createElement('div');
                                    actionsDiv.className = 'message-actions';
                                    actionsDiv.innerHTML = `
                                        <button class="btn-action" onclick="app.regenerateMessage(this)" title="Regenerate response">
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                                <polyline points="23 4 23 10 17 10"></polyline>
                                                <polyline points="1 20 1 14 7 14"></polyline>
                                                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                                            </svg>
                                        </button>
                                        <button class="btn-action" onclick="app.copyMessage(this)" title="Copy message">
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                                            </svg>
                                        </button>
                                    `;
                                    contentWrapper.appendChild(actionsDiv);
                                }
                                
                                requestAnimationFrame(() => {
                                    this.highlightCodeBlocks();
                                    this.scrollToBottom(true);
                                });
                                
                                break;
                            } else if (data.type === 'error') {
                                throw new Error(data.error || 'Streaming error');
                            }
                        } catch (e) {
                            if (e.message && e.message !== 'Streaming error') {
                                console.error('Error parsing SSE data:', e, line);
                            } else {
                                throw e;
                            }
                        }
                    }
                }
            }
            
            this.scrollToBottom();
            
        } catch (error) {
            this.removeLoading();
            this.removeThinkingIndicator();
            if (error.name === 'AbortError' || error.message.includes('aborted')) {
                const streamingMsg = document.querySelector('.message.streaming');
                if (streamingMsg) {
                    streamingMsg.classList.remove('streaming');
                }
            } else {
                this.showError(error.message || 'Streaming failed', error.errorData);
            }
            this.attachedFiles = filesToSend;
            this.updateFilePreview();
            throw error;
        } finally {
            this.isLoading = false;
            this.sendBtn.disabled = false;
            this.sendBtn.style.display = 'flex';
            this.stopBtn.style.display = 'none';
        }
    }
    
    async sendMessageNonStreaming(requestBody, filesToSend) {
        try {
            requestBody.stream = false;
            
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody),
                signal: this.abortController.signal
            });
            
            const data = await response.json();
            
            if (response.ok) {
                this.currentChatId = 'main_chat';
                this.removeLoading();
                
                this.addMessageToUI({
                    role: 'assistant',
                    content: data.response,
                    models: data.models || [data.model],
                    mode: data.mode,
                    routing_reasoning: data.routing_reasoning,
                    task_type: data.task_type,
                    individual_responses: data.individual_responses
                });
                
                this.scrollToBottom();
            } else {
                this.removeLoading();
                this.showError(data.error || 'An error occurred', data);
                this.attachedFiles = filesToSend;
                this.updateFilePreview();
            }
        } catch (error) {
            this.removeLoading();
            this.showError(error.message || 'Failed to send message. Make sure Ollama is running.', error.errorData);
            this.attachedFiles = filesToSend;
            this.updateFilePreview();
        } finally {
            this.isLoading = false;
            this.sendBtn.disabled = false;
        }
    }
    
    showLoading() {
        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'message assistant loading';
        loadingDiv.innerHTML = `
            <div class="message-avatar">✨</div>
            <div class="message-content">
                <div class="loading-message">
                    <div class="loading-dot"></div>
                    <div class="loading-dot"></div>
                    <div class="loading-dot"></div>
                </div>
            </div>
        `;
        this.chatMessages.appendChild(loadingDiv);
        this.scrollToBottom();
    }
    
    removeLoading() {
        const loading = this.chatMessages.querySelector('.message.loading');
        if (loading) loading.remove();
    }
    
    showError(message, errorData = null) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'message assistant';
        
        let errorContent = `<div class="message-content" style="color: var(--error-color);">`;
        errorContent += `<strong>⚠️ Error:</strong><br>`;
        errorContent += `<div style="margin-top: 8px; white-space: pre-wrap;">${this.escapeHtml(message)}</div>`;
        
        if (errorData) {
            if (errorData.install_command) {
                errorContent += `<div style="margin-top: 12px; padding: 12px; background: rgba(255, 106, 0, 0.1); border-left: 3px solid var(--primary-color); border-radius: 4px;">`;
                errorContent += `<strong style="color: var(--primary-color);">💡 To install the missing model:</strong><br>`;
                errorContent += `<code style="display: block; margin-top: 8px; padding: 8px; background: var(--bg-darker); border-radius: 4px;">${this.escapeHtml(errorData.install_command)}</code>`;
                errorContent += `</div>`;
            }
            if (errorData.recommended_models && errorData.recommended_models.length > 0) {
                errorContent += `<div style="margin-top: 12px; padding: 12px; background: rgba(255, 106, 0, 0.1); border-left: 3px solid var(--primary-color); border-radius: 4px;">`;
                errorContent += `<strong style="color: var(--primary-color);">✅ Available models you can use:</strong><br>`;
                errorData.recommended_models.forEach(model => {
                    errorContent += `<div style="margin-top: 4px;">• ${this.escapeHtml(model)}</div>`;
                });
                errorContent += `</div>`;
            }
        }
        
        errorContent += `</div>`;
        
        errorDiv.innerHTML = `
            <div class="message-avatar">✨</div>
            ${errorContent}
        `;
        
        this.chatMessages.appendChild(errorDiv);
        this.scrollToBottom();
    }
    
    logout() {
        if (confirm('Are you sure you want to logout?')) {
            window.location.href = '/logout';
        }
    }
    
    scrollToBottom(force = false) {
        if (!this.autoScrollEnabled && !force) return;
        if (this.isUserScrolling && !force) return;
        
        this.isScrollingProgrammatically = true;
        
        setTimeout(() => {
            if (this.autoScrollEnabled || force) {
                this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
            }
            
            if (force) {
                this.userScrolledUp = false;
                this.autoScrollEnabled = true;
                this.isUserScrolling = false;
            }
            
            setTimeout(() => {
                this.isScrollingProgrammatically = false;
            }, 100);
        }, 50);
    }
}

// Global function for copying code blocks
function copyCodeBlock(codeId) {
    const codeElement = document.getElementById(codeId);
    if (!codeElement) return;
    
    const text = codeElement.textContent;
    navigator.clipboard.writeText(text).then(() => {
        const button = event.target.closest('.btn-copy-code');
        if (button) {
            const originalHTML = button.innerHTML;
            button.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied!';
            button.style.color = 'var(--success-color)';
            setTimeout(() => {
                button.innerHTML = originalHTML;
                button.style.color = '';
            }, 2000);
        }
    }).catch(err => {
        console.error('Failed to copy code:', err);
    });
}

// Initialize the app when DOM is loaded
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new OllamaChatApp();
});