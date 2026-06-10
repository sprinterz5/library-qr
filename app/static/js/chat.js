/**
 * AI Librarian Chat Widget
 * Sends messages to /api/chat and displays responses.
 * Features: pill FAB, greeting bubble, suggestion chips, typing dots.
 */
(function() {
    'use strict';

    // ── Suggestion chips data ──
    const suggestions = [
        { label: '📚 Find a book',          query: 'Help me find a book' },
        { label: '📋 Borrowing rules',      query: 'What are the borrowing rules?' },
        { label: '📝 APA referencing help',  query: 'How do I reference in APA style?' }
    ];

    // ── Build HTML ──
    const chatHTML = `
        <button class="chat-fab" id="chatFab" title="Chat with AI Librarian">
            <span class="fab-icon">✨</span>
            <span class="fab-label">Ask AI</span>
        </button>
        <div class="chat-window" id="chatWindow">
            <div class="chat-header">
                <div class="chat-header-avatar">🤖</div>
                <div class="chat-header-text">
                    <h4>AI Librarian</h4>
                    <div class="chat-header-status">
                        <span class="status-dot"></span>
                        Online • Books, policies & referencing
                    </div>
                </div>
            </div>
            <div class="chat-messages" id="chatMessages">
                <div class="chat-msg bot">
                    👋 Hi! I'm the AI Librarian. I can help you find books, explain borrowing rules, answer referencing questions, and more!
                </div>
                <div class="chat-suggestions" id="chatSuggestions">
                    ${suggestions.map(s => `<button class="chat-chip" data-query="${s.query}">${s.label}</button>`).join('')}
                </div>
            </div>
            <div class="chat-input-area">
                <input type="text" class="chat-input" id="chatInput" placeholder="Ask me anything..." autocomplete="off">
                <button class="chat-send" id="chatSend" title="Send">➤</button>
            </div>
        </div>
    `;

    // Inject into page
    const container = document.createElement('div');
    container.innerHTML = chatHTML;
    document.body.appendChild(container);

    // ── Elements ──
    const fab       = document.getElementById('chatFab');
    const win       = document.getElementById('chatWindow');
    const messages  = document.getElementById('chatMessages');
    const input     = document.getElementById('chatInput');
    const sendBtn   = document.getElementById('chatSend');
    const chipBox   = document.getElementById('chatSuggestions');

    let isOpen    = false;
    let isSending = false;
    let chatHistory = [];

    // ── Greeting bubble (appears after 3s) ──
    function showGreetingBubble() {
        if (sessionStorage.getItem('chatBubbleDismissed')) return;
        if (isOpen) return;

        const bubble = document.createElement('div');
        bubble.className = 'chat-greeting-bubble';
        bubble.id = 'chatGreetingBubble';
        bubble.innerHTML = `
            <button class="bubble-dismiss" title="Dismiss">✕</button>
            <strong>Need help?</strong> Ask me about books, borrowing rules, or referencing — I'm an AI assistant! 💬
        `;
        document.body.appendChild(bubble);

        // Click bubble → open chat
        bubble.addEventListener('click', (e) => {
            if (e.target.classList.contains('bubble-dismiss')) {
                dismissBubble(bubble);
                return;
            }
            dismissBubble(bubble);
            openChat();
        });
    }

    function dismissBubble(bubble) {
        if (!bubble) return;
        sessionStorage.setItem('chatBubbleDismissed', '1');
        bubble.classList.add('hiding');
        setTimeout(() => bubble.remove(), 300);
    }

    setTimeout(showGreetingBubble, 3000);

    // ── Toggle chat ──
    function openChat() {
        isOpen = true;
        win.classList.add('visible');
        fab.classList.add('open');
        fab.innerHTML = '<span class="fab-icon">✕</span>';
        input.focus();
        // Remove greeting bubble if present
        const bubble = document.getElementById('chatGreetingBubble');
        if (bubble) dismissBubble(bubble);
    }

    function closeChat() {
        isOpen = false;
        win.classList.remove('visible');
        fab.classList.remove('open');
        fab.innerHTML = '<span class="fab-icon">✨</span><span class="fab-label">Ask AI</span>';
    }

    fab.addEventListener('click', () => {
        if (isOpen) closeChat();
        else openChat();
    });

    // ── Messages ──
    function addMessage(text, type) {
        const msg = document.createElement('div');
        msg.className = `chat-msg ${type}`;
        msg.textContent = text;
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
        return msg;
    }

    function addTypingIndicator() {
        const msg = document.createElement('div');
        msg.className = 'chat-msg bot loading';
        msg.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
        return msg;
    }

    async function sendMessage(text) {
        if (!text) text = input.value.trim();
        if (!text || isSending) return;

        isSending = true;
        sendBtn.disabled = true;
        input.value = '';

        // Hide suggestion chips after first interaction
        if (chipBox) chipBox.style.display = 'none';

        addMessage(text, 'user');
        chatHistory.push({ role: 'user', content: text });
        const loading = addTypingIndicator();

        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, history: chatHistory.slice(-6) })
            });

            if (!resp.ok) throw new Error(`Server error: ${resp.status}`);

            const data = await resp.json();
            loading.remove();
            const reply = data.reply || 'Sorry, I could not generate a response.';
            addMessage(reply, 'bot');
            chatHistory.push({ role: 'assistant', content: reply });
        } catch (err) {
            loading.remove();
            addMessage('Sorry, something went wrong. Please try again or contact library@coventry.edu.kz', 'bot');
            console.error('Chat error:', err);
        } finally {
            isSending = false;
            sendBtn.disabled = false;
            input.focus();
        }
    }

    // ── Suggestion chip clicks ──
    if (chipBox) {
        chipBox.addEventListener('click', (e) => {
            const chip = e.target.closest('.chat-chip');
            if (!chip) return;
            sendMessage(chip.dataset.query);
        });
    }

    // ── Send button / Enter key ──
    sendBtn.addEventListener('click', () => sendMessage());
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
})();
