// [SECTION: STATE]
let html5QrcodeScanner;
let currentStep = 'BIN'; // BIN or BAG
let scanType = null; // FWD or RTO
let scanData = {
    bin_id: null,
    bag_id: null,
    scan_type: null
};

// Recent scans history (max 5)
let recentScans = [];

// Scan queue for rapid consecutive scanning
let scanQueue = [];
let isProcessingQueue = false;
let queuedScansCount = 0;

// [SECTION: FEEDBACK]
// --- Audio & Haptic Feedback ---
function playSuccessSound() {
    // Create a simple beep using Web Audio API
    try {
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = audioContext.createOscillator();
        const gainNode = audioContext.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(audioContext.destination);

        oscillator.frequency.value = 800; // Frequency in Hz
        oscillator.type = 'sine';

        gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.1);

        oscillator.start(audioContext.currentTime);
        oscillator.stop(audioContext.currentTime + 0.1);
    } catch (e) {
        console.log('Audio not supported:', e);
    }
}

function triggerHapticFeedback() {
    // Vibrate for 200ms
    if ('vibrate' in navigator) {
        navigator.vibrate(200);
    }
}

function provideFeedback() {
    playSuccessSound();
    triggerHapticFeedback();
}

// [SECTION: UI]
// --- Recent Scans Management ---
function addToRecentScans(scan) {
    recentScans.unshift(scan); // Add to beginning
    if (recentScans.length > 5) {
        recentScans.pop(); // Remove oldest
    }
    updateRecentScansUI();
}

function updateRecentScansUI() {
    const scansList = document.getElementById('scans-list');
    const recentScansContainer = document.getElementById('recent-scans');

    if (recentScans.length === 0) {
        recentScansContainer.classList.add('hidden');
        return;
    }

    recentScansContainer.classList.remove('hidden');
    scansList.innerHTML = '';

    recentScans.forEach((scan, index) => {
        const scanItem = document.createElement('div');
        scanItem.className = 'scan-item';
        scanItem.innerHTML = `
            <div class="scan-info">
                <div class="scan-type">${scan.scan_type}</div>
                <div class="scan-ids">Bin: ${scan.bin_id} | Bag: ${scan.bag_id}</div>
            </div>
    }
});


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
// [SECTION: SCANNER]
function onScanSuccess(decodedText, decodedResult) {
    console.log(`Code matched = ${ decodedText } `, decodedResult);
    handleInput(decodedText);
}

function onScanFailure(error) {
    // console.warn(`Code scan error = ${ error } `);
}

function handleInput(text) {
    const statusArea = document.getElementById('status-area');
    statusArea.classList.add('hidden');

    if (currentStep === 'BIN') {
        // Validation: Check Bin ID length
        if (scanType === 'FWD' && text.length > 3) {
            showMessage('Error: FWD Bin ID cannot exceed 3 characters', 'error');
            return;
        }
        if (scanType === 'RTO' && text.length > 4) {
            showMessage('Error: RTO Bin ID cannot exceed 4 characters', 'error');
            return;
        }

        scanData.bin_id = text;
        currentStep = 'BAG';
        updateInstruction();
        showMessage(`Bin ${ text } scanned.Now scan Bag.`, 'success');
        document.getElementById('change-bin-btn').classList.remove('hidden');
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
        instr.innerText = `Bin: ${ scanData.bin_id } - Scan Bag`;
    }
}

function changeBin() {
    currentStep = 'BIN';
    scanData.bin_id = null;
    scanData.bag_id = null;
    updateInstruction();
    document.getElementById('change-bin-btn').classList.add('hidden');
    showMessage('Ready to scan new Bin.', 'success');
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
    el.className = `status ${ type } `;
    el.classList.remove('hidden');
}

async function submitData() {
    // Add to queue instead of submitting directly
    const scanToQueue = {
        bin_id: scanData.bin_id,
        bag_id: scanData.bag_id,
        scan_type: scanType,
        timestamp: new Date().toISOString()
    };

    scanQueue.push(scanToQueue);
    queuedScansCount++;

    // Immediate feedback (optimistic UI)
    provideFeedback();

    // Add to recent scans immediately
    addToRecentScans({
        scan_type: scanType,
        bin_id: scanData.bin_id,
        bag_id: scanData.bag_id
    });

    // Show success message immediately
    const queueInfo = scanQueue.length > 1 ? ` (${ scanQueue.length } in queue)` : '';
    showMessage(`✓ Bag ${ scanData.bag_id } queued${ queueInfo } `, 'success');

    // PERSISTENT BIN LOGIC:
    // Stay on 'BAG' step, keep bin_id, clear bag_id
    currentStep = 'BAG';
    scanData.bag_id = null;
    updateInstruction();

    // Auto-hide success message after 2s (faster for rapid scanning)
    setTimeout(() => {
        document.getElementById('status-area').classList.add('hidden');
    }, 2000);

    // Process queue in background
    processQueue();
}

// Process scan queue in background
async function processQueue() {
    if (isProcessingQueue || scanQueue.length === 0) return;

    isProcessingQueue = true;

    while (scanQueue.length > 0) {
        const scan = scanQueue[0]; // Peek at first item

        try {
            const response = await fetch('/record_scan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(scan),
            });

            const result = await response.json();

            if (result.status === 'success') {
                // Remove from queue after successful save
                scanQueue.shift();
                console.log(`✓ Saved: Bin ${ scan.bin_id } | Bag ${ scan.bag_id } `);
            } else {
                // Keep in queue and retry after delay
                console.error('Save failed, will retry:', result.message);
                await new Promise(resolve => setTimeout(resolve, 2000));
            }
        } catch (error) {
            // Network error - keep in queue and retry
            console.error('Network error, will retry:', error);
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
    }

    isProcessingQueue = false;

    // Show completion message if all queued scans are processed
    if (queuedScansCount > 0) {
        console.log(`✓ All ${ queuedScansCount } scans saved to server`);
        queuedScansCount = 0;
    }
}

// --- Scanner Control ---
function startScanner() {
    if (!html5QrcodeScanner) {
        html5QrcodeScanner = new Html5QrcodeScanner(
            "reader",
            {
                fps: 30,  // Increased from 10 to 30 for faster detection
                qrbox: { width: 250, height: 250 },
                aspectRatio: 1.0  // Optimize for square QR codes
            },
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
// Custom confirmation modal
function showConfirmModal(message) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirm-modal');
        const messageEl = document.getElementById('modal-message');
        const yesBtn = document.getElementById('modal-yes');
        const noBtn = document.getElementById('modal-no');
        const overlay = modal.querySelector('.modal-overlay');

        messageEl.textContent = message;
        modal.classList.remove('hidden');

        const handleYes = () => {
            cleanup();
            resolve(true);
        };

        const handleNo = () => {
            cleanup();
            resolve(false);
        };

        const cleanup = () => {
            modal.classList.add('hidden');
            yesBtn.removeEventListener('click', handleYes);
            noBtn.removeEventListener('click', handleNo);
            overlay.removeEventListener('click', handleNo);
        };

        yesBtn.addEventListener('click', handleYes);
        noBtn.addEventListener('click', handleNo);
        overlay.addEventListener('click', handleNo);
    });
}

// Download Modal Functions
function showDownloadModal() {
    const modal = document.getElementById('download-modal');
    const today = new Date().toISOString().split('T')[0];

    // Set default dates to today
    document.getElementById('start-date').value = today;
    document.getElementById('end-date').value = today;

    modal.classList.remove('hidden');
}

function closeDownloadModal() {
    const modal = document.getElementById('download-modal');
    modal.classList.add('hidden');
}

async function handleDownload() {
    const startDate = document.getElementById('start-date').value;
    const endDate = document.getElementById('end-date').value;
    const branch = localStorage.getItem('userBranch') || 'Unknown';

    // Validate dates
    if (!startDate || !endDate) {
        showMessage('Please select both start and end dates', 'error');
        return;
    }

    const start = new Date(startDate);
    const end = new Date(endDate);

    // Check if start date is before end date
    if (start > end) {
        showMessage('Start date must be before or equal to end date', 'error');
        return;
    }

    // Check 7-day limit
    const daysDiff = Math.ceil((end - start) / (1000 * 60 * 60 * 24));
    if (daysDiff > 7) {
        showMessage('Date range cannot exceed 7 days', 'error');
        return;
    }

    try {
        showMessage('Preparing download...', 'success');

        const response = await fetch('/download_data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                start_date: startDate,
                end_date: endDate,
                branch: branch
            }),
        });

        // Check if response is JSON (error) or file (success)
        const contentType = response.headers.get('content-type');

        if (contentType && contentType.includes('application/json')) {
            // Error response
            const result = await response.json();
            showMessage(result.message || 'Download failed', 'error');
            return;
        }

        // Success - download file
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;

        // Extract filename from Content-Disposition header
        const disposition = response.headers.get('Content-Disposition');
        let filename = 'scan_data.xlsx';
        if (disposition && disposition.includes('filename=')) {
            filename = disposition.split('filename=')[1].replace(/"/g, '');
        }

        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showMessage('Download complete!', 'success');
        closeDownloadModal();

    } catch (error) {
        console.error('Download error:', error);
        showMessage('Network error while downloading', 'error');
    }
}
