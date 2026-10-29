(function () {
    "use strict";

    const form = document.getElementById("composer");
    const input = document.getElementById("message-input");
    const sendButton = document.getElementById("send-button");
    const list = document.getElementById("messages");
    const template = document.getElementById("message-template");
    const emptyState = document.getElementById("empty-state");
    const scroller = document.getElementById("chat-scroll");
    const newChat = document.getElementById("new-chat");

    if (!form || !input || !list || !template) {
        console.error("TradeChecker: chat markup is missing, the UI cannot start.");
        return;
    }

    const MAX_HISTORY = 40;
    const MAX_INPUT_HEIGHT = 168;

    const history = [];
    let busy = false;

    function clock() {
        return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function scrollToBottom() {
        if (scroller) {
            scroller.scrollTop = scroller.scrollHeight;
        }
    }

    function setBusy(value) {
        busy = value;
        form.setAttribute("aria-busy", String(value));
        if (sendButton) {
            sendButton.disabled = value;
        }
    }

    function resize() {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, MAX_INPUT_HEIGHT) + "px";
    }

    function hideEmptyState() {
        if (emptyState) {
            emptyState.hidden = true;
        }
    }

    function showEmptyState() {
        if (emptyState) {
            emptyState.hidden = false;
        }
    }

    function createMessage(role, text) {
        const node = template.content.firstElementChild.cloneNode(true);
        node.dataset.role = role;
        node.querySelector(".message-author").textContent =
            role === "user" ? "You" : "TradeChecker";
        node.querySelector(".message-time").textContent = clock();
        node.querySelector(".bubble-text").textContent = text;
        list.appendChild(node);
        return node;
    }

    function createTyping() {
        const node = document.createElement("li");
        node.className = "message is-typing";
        node.dataset.role = "assistant";

        const avatar = document.createElement("div");
        avatar.className = "avatar";
        avatar.setAttribute("aria-hidden", "true");

        const body = document.createElement("div");
        body.className = "message-body";

        const meta = document.createElement("div");
        meta.className = "message-meta";
        const author = document.createElement("span");
        author.className = "message-author";
        author.textContent = "TradeChecker";
        meta.appendChild(author);

        const bubble = document.createElement("div");
        bubble.className = "bubble";
        for (let i = 0; i < 3; i += 1) {
            const dot = document.createElement("span");
            dot.className = "typing-dot";
            bubble.appendChild(dot);
        }

        const label = document.createElement("span");
        label.className = "sr-only";
        label.textContent = "TradeChecker is typing";
        bubble.appendChild(label);

        body.append(meta, bubble);
        node.append(avatar, body);
        list.appendChild(node);
        return node;
    }

    function createError(message, retryText) {
        const node = createMessage("assistant", message);
        node.classList.add("is-error");

        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "retry-button";
        retry.textContent = "Try again";
        retry.addEventListener("click", function () {
            node.remove();
            submit(retryText, false);
        });

        node.querySelector(".bubble").appendChild(retry);
        return node;
    }

    function describeTools(toolCalls) {
        const symbols = [];
        const names = [];

        function remember(list_, value) {
            if (value && !list_.includes(value)) {
                list_.push(value);
            }
        }

        toolCalls.forEach(function (call) {
            remember(names, call.name);
            const args = call.arguments || {};
            if (typeof args.symbol === "string") {
                remember(symbols, args.symbol.toUpperCase());
            }
            if (Array.isArray(args.symbols)) {
                args.symbols.forEach(function (symbol) {
                    remember(symbols, String(symbol).toUpperCase());
                });
            }
            if (typeof args.query === "string") {
                remember(symbols, args.query);
            }
        });

        const label = symbols.length ? symbols.join(", ") : names.join(", ");
        return "Checked " + label + " \u00b7 delayed";
    }

    function addToolNote(node, toolCalls) {
        const note = document.createElement("p");
        note.className = "tool-note";
        note.textContent = describeTools(toolCalls);
        node.querySelector(".message-body").appendChild(note);
    }

    async function submit(text, echo) {
        const message = (text || "").trim();
        if (!message || busy) {
            return;
        }

        hideEmptyState();
        if (echo !== false) {
            createMessage("user", message);
        }

        setBusy(true);
        input.value = "";
        resize();

        const typing = createTyping();
        scrollToBottom();

        try {
            const response = await fetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text: message,
                    messages: history.slice(-MAX_HISTORY),
                }),
            });

            const data = await response.json().catch(function () {
                return {};
            });

            typing.remove();

            if (!response.ok) {
                throw new Error(data.error || "Request failed (" + response.status + ").");
            }

            const reply = data.reply || "I don't have an answer for that.";
            const node = createMessage("assistant", reply);
            if (Array.isArray(data.tool_calls) && data.tool_calls.length) {
                addToolNote(node, data.tool_calls);
            }

            history.push({ role: "user", content: message });
            history.push({ role: "assistant", content: reply });
            if (history.length > MAX_HISTORY) {
                history.splice(0, history.length - MAX_HISTORY);
            }
        } catch (error) {
            typing.remove();
            createError(error.message || "Something went wrong.", message);
        } finally {
            setBusy(false);
            scrollToBottom();
            input.focus();
        }
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        submit(input.value);
    });

    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            submit(input.value);
        }
    });

    input.addEventListener("input", resize);

    if (newChat) {
        newChat.addEventListener("click", function () {
            history.length = 0;
            list.replaceChildren();
            showEmptyState();
            input.value = "";
            resize();
            input.focus();
        });
    }

    resize();
    if (window.matchMedia("(pointer: fine)").matches) {
        input.focus();
    }
})();
