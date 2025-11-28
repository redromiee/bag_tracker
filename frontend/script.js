let html5QrcodeScanner;
let currentStep = 'BIN'; // BIN or BAG
let scanType = null; // FWD or RTO
let scanData = {
    bin_id: null,
    bag_id: null,
    scan_type: null
};

// --- Theme Toggle Logic ---
function toggleTheme() {
    const body = document.body;
    const isDark = body.classList.toggle('dark-mode');

    // Save preference to localStorage
    localStorage.setItem('theme', isDark ? 'dark' : 'light');

    // Update toggle icon
    updateThemeIcon(isDark);
}

function updateThemeIcon(isDark) {
    const icon = document.querySelector('.toggle-icon');
    icon.textContent = isDark ? '🌙' : '☀️';
}

// Load saved theme on page load
function loadTheme() {
    const savedTheme = localStorage.getItem('theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    // Use saved theme, or fall back to system preference
    const isDark = savedTheme === 'dark' || (!savedTheme && prefersDark);

    if (isDark) {
        document.body.classList.add('dark-mode');
    }

    updateThemeIcon(isDark);
}

// Initialize theme on load
loadTheme();


// --- Selection Screen Logic ---
function selectType(type) {
    scanType = type;
    document.getElementById('selection-screen').classList.add('hidden');
    document.getElementById('scanning-screen').classList.remove('hidden');
    document.getElementById('mode-title').innerText = type;

    // Reset scan flow
    currentStep = 'BIN';
    updateInstruction();
    startScanner(); // Ensure scanner is running
}

function resetToSelection() {
    document.getElementById('scanning-screen').classList.add('hidden');
    document.getElementById('selection-screen').classList.remove('hidden');
    scanType = null;
    stopScanner(); // Optional: stop scanner to save battery/resources
}

// --- Scanning Logic ---
function onScanSuccess(decodedText, decodedResult) {
    console.log(`Code matched = ${decodedText}`, decodedResult);
    handleInput(decodedText);
}

function onScanFailure(error) {
    // console.warn(`Code scan error = ${error}`);
}

function handleInput(text) {
    const statusArea = document.getElementById('status-area');
    statusArea.classList.add('hidden');

    if (currentStep === 'BIN') {
        scanData.bin_id = text;
        currentStep = 'BAG';
        updateInstruction();
        showMessage(`Bin ${text} scanned. Now scan Bag.`, 'success');
    } else if (currentStep === 'BAG') {
        scanData.bag_id = text;
        submitData();
    }
}

function updateInstruction() {
    const instr = document.getElementById('current-instruction');
    if (currentStep === 'BIN') {
        instr.innerText = "Scan Bin QR";
    } else {
        instr.innerText = `Bin: ${scanData.bin_id} - Scan Bag`;
    }
}

function handleManualInput() {
    const input = document.getElementById('manual-input');
    if (input.value) {
        handleInput(input.value);
        input.value = '';
    }
}

// Enter Key Support
document.getElementById('manual-input').addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
        handleManualInput();
    }
});

function showMessage(msg, type) {
    const el = document.getElementById('status-area');
    el.innerText = msg;
    el.className = `status ${type}`;
    el.classList.remove('hidden');
}

async function submitData() {
    showMessage('Submitting...', 'success');

    scanData.scan_type = scanType;

    try {
        const response = await fetch('/record_scan', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(scanData),
        });

        const result = await response.json();

        if (result.status === 'success') {
            showMessage(`Saved! ${scanType} | Bin: ${scanData.bin_id} | Bag: ${scanData.bag_id}`, 'success');

            // Reset for next bag, keep same Bin? Or reset all?
            // Usually better to reset to Bin for safety, or keep Bin if doing multiple bags per bin.
            // Let's reset to Bin for now as per "move to scanning Bin name" request.
            currentStep = 'BIN';
            scanData.bin_id = null;
            scanData.bag_id = null;
            updateInstruction();

            // Optional: Auto-hide success message after 3s
            setTimeout(() => {
                document.getElementById('status-area').classList.add('hidden');
            }, 3000);

        } else {
            showMessage('Error saving data.', 'error');
        }
    } catch (error) {
        console.error('Error:', error);
        showMessage('Network error.', 'error');
    }
}

// --- Scanner Control ---
function startScanner() {
    if (!html5QrcodeScanner) {
        html5QrcodeScanner = new Html5QrcodeScanner(
            "reader",
            { fps: 10, qrbox: { width: 250, height: 250 } },
            /* verbose= */ false);
        html5QrcodeScanner.render(onScanSuccess, onScanFailure);
    }
}

function stopScanner() {
    if (html5QrcodeScanner) {
        html5QrcodeScanner.clear().then(_ => {
            html5QrcodeScanner = null;
        }).catch(error => {
            console.error("Failed to clear html5QrcodeScanner. ", error);
        });
    }
}

// Start scanner on load? No, wait for selection.
// Actually, html5-qrcode needs the element to exist. The element is in #scanning-screen.
// We should init it when showing scanning screen.
