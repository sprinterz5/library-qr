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
    suggestions.push(
        { label: 'AI & data science', query: 'Find books about artificial intelligence and data science' },
        { label: 'Suggest a story', query: 'Find me a good story book to read' },
        { label: 'Programming', query: 'Find books about programming and software engineering' },
        { label: 'Cybersecurity', query: 'Find books about cybersecurity' },
        { label: 'Business & finance', query: 'Find books about business and finance' },
        { label: 'Academic writing', query: 'Find books about academic writing and English' },
        { label: 'Library hours', query: 'What are the library opening hours?' },
        { label: 'Accounting', query: 'Find books about accounting' },
        { label: 'Economics', query: 'Find books about economics' },
        { label: 'Marketing', query: 'Find books about marketing' },
        { label: 'Management', query: 'Find books about management and leadership' },
        { label: 'Entrepreneurship', query: 'Find books about entrepreneurship' },
        { label: 'Mathematics', query: 'Find books about mathematics' },
        { label: 'Statistics', query: 'Find books about statistics' },
        { label: 'Psychology', query: 'Find books about psychology' },
        { label: 'Education', query: 'Find books about education and teaching' },
        { label: 'Research methods', query: 'Find books about research methods' },
        { label: 'Law', query: 'Find books about law' },
        { label: 'International relations', query: 'Find books about international relations' },
        { label: 'Media & communication', query: 'Find books about media and communication' },
        { label: 'Design', query: 'Find books about graphic design and user experience' },
        { label: 'Health', query: 'Find books about health and wellbeing' },
        { label: 'Environment', query: 'Find books about environmental science and sustainability' },
        { label: 'Engineering', query: 'Find books about engineering' },
        { label: 'Architecture', query: 'Find books about architecture' },
        { label: 'History', query: 'Find books about history' },
        { label: 'Politics', query: 'Find books about politics' },
        { label: 'Sociology', query: 'Find books about sociology' },
        { label: 'Languages', query: 'Find books about language learning and linguistics' },
        { label: 'Literature', query: 'Find books about literature' },
        { label: 'Art & music', query: 'Find books about art and music' },
        { label: 'Tourism', query: 'Find books about tourism and hospitality' },
        { label: 'Sport', query: 'Find books about sport and exercise' }
    );

    const chatHTML = `
        <button class="chat-fab" id="chatFab" title="Chat with AI Librarian" aria-label="Open AI Librarian chat" aria-expanded="false">
            <span class="fab-icon">✨</span>
            <span class="fab-label">Ask AI</span>
        </button>
        <div class="chat-window" id="chatWindow" role="dialog" aria-label="AI Librarian chat">
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
            <div class="chat-messages" id="chatMessages" aria-live="polite" aria-relevant="additions">
                <div class="chat-msg bot">
                    👋 Hi! I'm the AI Librarian. I can help you find books, explain borrowing rules, answer referencing questions, and more!
                </div>
                <div class="chat-suggestions" id="chatSuggestions">
                    ${suggestions.map(s => `<button class="chat-chip" data-query="${s.query}">${s.label}</button>`).join('')}
                </div>
            </div>
            <div class="chat-input-area">
                <select id="chatLanguage" aria-label="Response language"><option value="">Auto</option><option>English</option><option>Russian</option><option>Kazakh</option></select>
                <input type="text" class="chat-input" id="chatInput" list="chatAutocomplete" placeholder="Ask me anything..." autocomplete="off"><datalist id="chatAutocomplete"></datalist>
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
    const language  = document.getElementById('chatLanguage');
    const autocomplete = document.getElementById('chatAutocomplete');

    let isOpen    = false;
    let isSending = false;
    let chatHistory = [];
    const maxHistoryMessages = 6;
    const requestTimeoutMs = 90000;
    const maxMessageChars = 1000;

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
        fab.setAttribute('aria-expanded', 'true');
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
        fab.setAttribute('aria-expanded', 'false');
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

    function addFeedbackControls() {
        const wrap = document.createElement('div');
        wrap.className = 'chat-feedback';
        wrap.innerHTML = '<button type="button" aria-label="Helpful answer">Helpful</button><button type="button" aria-label="Not helpful answer">Not helpful</button>';
        wrap.addEventListener('click', (event) => {
            const button = event.target.closest('button');
            if (!button) return;
            wrap.textContent = 'Thanks for the feedback.';
        });
        messages.appendChild(wrap);
    }

    function availabilityText(source) {
        const status = source.availability_status || '';
        const count = Number.isFinite(Number(source.available_count)) ? Number(source.available_count) : null;
        if (status === 'available') {
            if (count === null) return 'Available';
            return count === 1 ? 'Available: 1 copy' : `Available: ${count} copies`;
        }
        if (status === 'unavailable') {
            return 'Currently unavailable';
        }
        return 'Availability: check eLibra or ask the library desk';
    }

    function addBookCards(sources) {
        const books = (sources || []).filter((source) => {
            if (!source || !source.title) return false;
            return source.source_type === 'book' || Boolean(source.classification_number);
        });
        if (!books.length) return null;

        const wrap = document.createElement('div');
        wrap.className = 'chat-book-cards';

        books.slice(0, 5).forEach((source) => {
            const card = document.createElement('div');
            card.className = 'chat-book-card';

            const title = document.createElement('div');
            title.className = 'chat-book-title';
            title.textContent = source.title || 'Untitled';
            card.appendChild(title);

            const meta = document.createElement('div');
            meta.className = 'chat-book-meta';
            meta.textContent = source.author ? `Author: ${source.author}` : 'Author: not listed in the catalog';
            card.appendChild(meta);

            const shelf = document.createElement('div');
            shelf.className = 'chat-book-shelf';
            shelf.textContent = `Classification number: ${source.classification_number || 'not listed'}`;
            card.appendChild(shelf);

            const availability = document.createElement('div');
            availability.className = `chat-book-availability ${source.availability_status || 'unknown'}`;
            availability.textContent = availabilityText(source);
            card.appendChild(availability);

            if (source.url) {
                const link = document.createElement('a');
                link.className = 'chat-book-link';
                link.href = source.url;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = 'Reserve or view in eLibra';
                link.setAttribute('aria-label', `Reserve or view ${source.title || 'this book'} in eLibra`);
                card.appendChild(link);
            }

            wrap.appendChild(card);
        });

        messages.appendChild(wrap);
        messages.scrollTop = messages.scrollHeight;
        return wrap;
    }

    function hasBookSources(sources) {
        return (sources || []).some((source) =>
            source && source.title && (source.source_type === 'book' || Boolean(source.classification_number))
        );
    }

    function addTypingIndicator() {
        const msg = document.createElement('div');
        msg.className = 'chat-msg bot loading';
        msg.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
        return msg;
    }

    function trimChatHistory() {
        if (chatHistory.length > maxHistoryMessages) {
            chatHistory = chatHistory.slice(-maxHistoryMessages);
        }
    }

    async function sendMessage(text) {
        if (!text) text = input.value.trim();
        if (!text || isSending) return;
        text = text.slice(0, maxMessageChars);

        isSending = true;
        sendBtn.disabled = true;
        input.disabled = true;
        input.value = '';

        // Hide suggestion chips after first interaction
        if (chipBox) chipBox.style.display = 'none';

        addMessage(text, 'user');
        chatHistory.push({ role: 'user', content: text });
        trimChatHistory();
        const loading = addTypingIndicator();
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), requestTimeoutMs);

        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, history: chatHistory.slice(-maxHistoryMessages), language: language.value }),
                signal: controller.signal
            });

            if (!resp.ok) throw new Error(`Server error: ${resp.status}`);

            const data = await resp.json();
            loading.remove();
            const reply = data.reply || 'Sorry, I could not generate a response.';
            // Catalog responses already appear as book cards; do not render the
            // formatted text list as well, otherwise every result is duplicated.
            if (hasBookSources(data.sources)) {
                addBookCards(data.sources);
            } else {
                addMessage(reply, 'bot');
                addFeedbackControls();
            }
            chatHistory.push({ role: 'assistant', content: reply, sources: data.sources || [] });
            trimChatHistory();
        } catch (err) {
            loading.remove();
            const message = err.name === 'AbortError'
                ? 'The request took too long. Please try again with a shorter question, or contact library@coventry.edu.kz.'
                : 'Sorry, something went wrong. Please try again or contact library@coventry.edu.kz';
            addMessage(message, 'bot');
            console.error('Chat error:', err);
        } finally {
            clearTimeout(timeoutId);
            isSending = false;
            sendBtn.disabled = false;
            input.disabled = false;
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
    input.addEventListener('input', async () => {
        if (input.value.trim().length < 2) return;
        const response = await fetch(`/api/chat/suggestions?q=${encodeURIComponent(input.value.trim())}`);
        if (!response.ok) return;
        const data = await response.json();
        autocomplete.replaceChildren(...(data.suggestions || []).map((value) => {
            const option = document.createElement('option'); option.value = value; return option;
        }));
    });
})();
