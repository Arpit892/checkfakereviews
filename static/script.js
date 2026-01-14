function analyze() {
    // 1. Get Elements
    const urlInput = document.getElementById('urlInput');
    const btn = document.getElementById('scanBtn');
    const loader = document.getElementById('btnLoader');
    const resultBox = document.getElementById('result');

    // 2. Validation
    const url = urlInput.value.trim();
    if (!url) {
        alert("Please paste a valid product URL first!");
        return;
    }

    // 3. UI: Set to "Loading" State
    btn.disabled = true;
    btn.innerText = "Scanning...";
    loader.classList.remove('hidden'); // Show spinner
    resultBox.classList.add('hidden'); // Hide old results

    console.log("Sending request to server...");

    // 4. Send Data to Python Backend
    // FIX: Changed to relative path so it works on Cloud (Render) AND Localhost
    fetch('/analyze', { 
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ url: url })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error("Server responded with an error!");
        }
        return response.json();
    })
    .then(data => {
        // 5. UI: Reset to "Active" State
        btn.disabled = false;
        btn.innerText = "Analyze";
        loader.classList.add('hidden'); // Hide spinner
        resultBox.classList.remove('hidden'); // Show results

        // 6. Update the Text Data
        document.getElementById('totalReviews').innerText = data.total;
        document.getElementById('realCount').innerText = data.real;
        document.getElementById('fakeCount').innerText = data.fake;
        
        // Trust Score & Verdict
        const scoreText = document.getElementById('trustScore');
        scoreText.innerText = data.score;
        
        const badge = document.getElementById('verdictBadge');
        badge.innerText = data.verdict;

        // 7. Update Colors & Progress Bar based on Score
        const progressBar = document.getElementById('scoreBar');
        
        // Reset classes first
        badge.className = 'verdict-badge'; 
        
        if (data.score >= 80) {
            // SAFE (Green)
            badge.classList.add('badge-safe');
            scoreText.style.color = '#059669'; 
            progressBar.style.backgroundColor = '#059669';
        } else if (data.score >= 50) {
            // CAUTION (Orange)
            badge.classList.add('badge-warn');
            scoreText.style.color = '#d97706'; 
            progressBar.style.backgroundColor = '#d97706';
        } else {
            // DANGER (Red)
            badge.classList.add('badge-risk');
            scoreText.style.color = '#dc2626'; 
            progressBar.style.backgroundColor = '#dc2626';
        }

        // Animate the bar width (0% -> Score%)
        progressBar.style.width = "0%";
        setTimeout(() => {
            progressBar.style.width = data.score + "%";
        }, 100);

    })
    .catch(error => {
        console.error("Connection Error:", error);
        
        // Reset UI on Error
        btn.disabled = false;
        btn.innerText = "Analyze";
        loader.classList.add('hidden');
        
        alert("Could not connect to the server!\n\nIf you are seeing this on Render, wait 30 seconds and try again (the server might be waking up).");
    });
}
