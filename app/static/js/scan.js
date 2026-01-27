const KEY_READER = "elibra_reader_data";
const CARDCODE_PREFIX = window.CARDCODE_PREFIX || "21000000";

function qs(name) {
    return new URLSearchParams(window.location.search).get(name) || "";
}
function setStatus(msg, isLoading = false) {
    const statusEl = document.getElementById("status");
    statusEl.innerText = msg || "";
    if (isLoading) {
        statusEl.className = "status-loading";
    } else {
        statusEl.className = "";
    }
}

function loadSavedReader() {
    try {
        const saved = localStorage.getItem(KEY_READER);
        if (saved) {
            const data = JSON.parse(saved);
            if (data.card_barcode) {
                document.getElementById("card_barcode").value = String(data.card_barcode);
                const cardcode = String(data.card_barcode);
                if (cardcode.length >= 5) {
                    const suffix = cardcode.slice(-5);
                    document.getElementById("cardcodeSuffix").value = suffix;
                    if (data.name && data.card_barcode) {
                        document.getElementById("readerName").innerText = data.name + ": " + data.card_barcode;
                        document.getElementById("readerCardcode").innerText = "Cardcode: " + data.card_barcode;
                        document.getElementById("readerResult").style.display = "block";
                    }
                }
            }
            if (data.reader_id) {
                document.getElementById("reader_id").value = String(data.reader_id);
            }
        }
    } catch (e) {
        console.error("Error loading saved reader:", e);
    }
}

function clearReader() {
    localStorage.removeItem(KEY_READER);
    document.getElementById("reader_id").value = "";
    document.getElementById("card_barcode").value = "";
    document.getElementById("cardcodeSuffix").value = "";
    document.getElementById("readerResult").style.display = "none";
    setStatus("Reader cleared. Введите последние 5 цифр cardcode.");
}

function clearBarcode() {
    document.getElementById("barcode").value = "";
    setStatus("Barcode очищен");
}

let isSearchingReader = false;
let isSubmitting = false;

async function searchByCardcodeSuffix(suffix) {
    if (isSearchingReader) return;
    if (!suffix || suffix.length !== 5) {
        setStatus("Введите ровно 5 цифр");
        return;
    }

    const fullCardcode = CARDCODE_PREFIX + suffix;
    isSearchingReader = true;
    const input = document.getElementById("cardcodeSuffix");
    input.disabled = true;
    setStatus("🔎 Проверяю cardcode…", true);
    document.getElementById("readerResult").style.display = "none";

    try {
        const res = await fetch(`/api/readers/search-by-cardcode?cardcode=${encodeURIComponent(fullCardcode)}`);
        const data = await res.json();

        if (data.ok && data.result) {
            const item = data.result;
            const readerId = item.parentId;
            const fm = (item.fieldModels || []);
            const getByCode = (code) => {
                const f = fm.find(x => x.code === code);
                return f ? f.value : "";
            };

            const first = getByCode("FIRST_NAME");
            const last = getByCode("LAST_NAME");
            const card = getByCode("LIBRARY_CARD_BARCODE") || fullCardcode;
            const name = `${first || ""} ${last || ""}`.trim() || "Unknown";
            const readerData = {
                card_barcode: card,
                reader_id: String(readerId),
                name: name
            };
            localStorage.setItem(KEY_READER, JSON.stringify(readerData));
            document.getElementById("card_barcode").value = String(card);
            document.getElementById("reader_id").value = String(readerId);
            document.getElementById("readerName").innerText = name + ": " + card;
            document.getElementById("readerCardcode").innerText = "Cardcode: " + card;
            document.getElementById("readerResult").style.display = "block";

            setStatus("✅ Читатель найден");
        } else {
            setStatus("❌ Читатель не найден. Проверьте cardcode.");
            document.getElementById("readerResult").style.display = "none";
        }
    } catch (error) {
        setStatus("Ошибка при поиске. Попробуйте ещё раз.");
        console.error("Search error:", error);
        document.getElementById("readerResult").style.display = "none";
    } finally {
        isSearchingReader = false;
        input.disabled = false;
    }
}

function submitAction(action) {
    if (isSubmitting) return;

    const barcode = (document.getElementById("barcode").value || "").trim();
    const cardBarcode = (document.getElementById("card_barcode").value || "").trim();

    if (!barcode) {
        setStatus("Нужен barcode книги");
        return;
    }
    if (action === "issue" && !cardBarcode) {
        setStatus("Для Issue нужно выбрать читателя (card barcode). Нажми 'Найти'.");
        return;
    }

    isSubmitting = true;
    const btnIssue = document.getElementById("btnIssue");
    const btnReturn = document.getElementById("btnReturn");
    btnIssue.disabled = true;
    btnReturn.disabled = true;
    btnIssue.classList.add("loading");
    btnReturn.classList.add("loading");

    if (action === "issue") {
        setStatus("⏳ Оформляем выдачу...", true);
    } else {
        setStatus("⏳ Отправляем заявку на возврат...", true);
    }

    document.getElementById("action").value = action;
    document.getElementById("deskForm").submit();
}

document.addEventListener("DOMContentLoaded", () => {
    const cardcodeInput = document.getElementById("cardcodeSuffix");
    cardcodeInput.addEventListener("input", (e) => {
        e.target.value = e.target.value.replace(/[^0-9]/g, "").slice(0, 5);
        if (e.target.value.length === 5) {
            searchByCardcodeSuffix(e.target.value);
        } else {
            document.getElementById("readerResult").style.display = "none";
            document.getElementById("card_barcode").value = "";
            document.getElementById("reader_id").value = "";
        }
    });
    cardcodeInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target.value.length === 5) {
            e.preventDefault();
            searchByCardcodeSuffix(e.target.value);
        }
    });

    const b = qs("barcode");
    if (b) document.getElementById("barcode").value = b;
    loadSavedReader();

    let html5QrcodeScanner = null;
    const barcodeInput = document.getElementById("barcode");
    const qrCameraBtn = document.getElementById("qr-camera-btn");
    const qrReaderDiv = document.getElementById("qr-reader");

    if (typeof Html5Qrcode === "undefined") {
        setStatus("❌ Библиотека QR-сканера не загружена. Проверьте интернет-соединение.");
    }

    function extractBarcodeFromUrl(text) {
        try {
            if (text.includes("barcode=")) {
                const url = new URL(text);
                const barcode = url.searchParams.get("barcode");
                if (barcode) {
                    return barcode;
                }
            }
            return text;
        } catch (e) {
            if (text.includes("barcode=")) {
                const match = text.match(/[?&]barcode=([^&]*)/);
                if (match && match[1]) {
                    return decodeURIComponent(match[1]);
                }
            }
            return text;
        }
    }

    const handleCameraClick = async (e) => {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }

        if (typeof Html5Qrcode === "undefined") {
            setStatus("❌ Библиотека QR-сканера не загружена");
            return;
        }

        // Check if camera API is available
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
            const isHttps = window.location.protocol === "https:";
            if (!isLocalhost && !isHttps) {
                setStatus("❌ Для доступа к камере нужен HTTPS или localhost. Открой через http://localhost:8000/scan");
            } else {
                setStatus("❌ Ваш браузер не поддерживает доступ к камере");
            }
            return;
        }

        if (html5QrcodeScanner) {
            try {
                await html5QrcodeScanner.stop();
                await html5QrcodeScanner.clear();
                html5QrcodeScanner = null;
                qrReaderDiv.style.display = "none";
                qrCameraBtn.style.display = "flex";
                return;
            } catch (e) {
                console.error("Error stopping scanner:", e);
            }
        }

        try {
            qrCameraBtn.style.display = "none";
            qrReaderDiv.style.display = "block";
            qrReaderDiv.classList.add("active");
            html5QrcodeScanner = new Html5Qrcode("qr-reader");

            await html5QrcodeScanner.start(
                { facingMode: "environment" },
                {
                    fps: 10,
                    qrbox: { width: 180, height: 180 }
                },
                (decodedText) => {
                    const barcode = extractBarcodeFromUrl(decodedText);
                    barcodeInput.value = barcode;
                    html5QrcodeScanner.stop().then(() => {
                        html5QrcodeScanner.clear();
                        html5QrcodeScanner = null;
                        qrReaderDiv.style.display = "none";
                        qrReaderDiv.classList.remove("active");
                        qrCameraBtn.style.display = "flex";
                        setStatus("✅ QR-код отсканирован");
                    }).catch((e) => {
                        console.error("Error stopping scanner after success:", e);
                    });
                },
                (errorMessage) => {
                    // Silent error handling
                }
            );
        } catch (err) {
            let errorMsg = "Неизвестная ошибка";
            if (err && err.message) {
                errorMsg = err.message;
            } else if (err && err.toString) {
                errorMsg = err.toString();
            } else if (typeof err === "string") {
                errorMsg = err;
            }

            // More specific error messages
            if (errorMsg.includes("Permission denied") || errorMsg.includes("NotAllowedError")) {
                errorMsg = "Разрешение на камеру отклонено. Разрешите доступ в настройках браузера";
            } else if (errorMsg.includes("NotFoundError") || errorMsg.includes("No camera")) {
                errorMsg = "Камера не найдена";
            } else if (errorMsg.includes("NotReadableError") || errorMsg.includes("TrackStartError")) {
                errorMsg = "Камера занята другим приложением";
            } else if (errorMsg.includes("OverconstrainedError")) {
                errorMsg = "Камера не поддерживает требуемые параметры";
            }

            setStatus("❌ Ошибка доступа к камере: " + errorMsg);
            qrReaderDiv.style.display = "none";
            qrReaderDiv.classList.remove("active");
            qrCameraBtn.style.display = "flex";
            html5QrcodeScanner = null;
        }
    };

    // Support both click and touch events for mobile
    qrCameraBtn.addEventListener("click", (e) => handleCameraClick(e));
    qrCameraBtn.addEventListener("touchstart", (e) => {
        e.preventDefault();
        handleCameraClick(e);
    }, { passive: false });
    qrCameraBtn.addEventListener("touchend", (e) => {
        e.preventDefault();
        handleCameraClick(e);
    }, { passive: false });
});
