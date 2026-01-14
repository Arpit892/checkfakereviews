function analyze() {
    const url = document.getElementById('urlInput').value;
    const loading = document.getElementById('loading');
    const resultDiv = document.getElementById('result');

    if(!url) { alert("Please paste a URL first!"); return; }

    loading.classList.remove('hidden');
    resultDiv.classList.add('hidden');

    fetch('/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url })
    })
    .then(response => response.json())
    .then(data => {
        loading.classList.add('hidden');
        resultDiv.classList.remove('hidden');
        
        // Update Stats
        document.getElementById('totalReviews').innerText = data.total;
        document.getElementById('realCount').innerText = data.real;
        document.getElementById('fakeCount').innerText = data.fake;

        // Calculate Trust Score
        let trustScore = 0;
        if(data.total > 0) {
            trustScore = Math.round((data.real / data.total) * 100);
        }

        const scoreEl = document.getElementById('trustScore');
        const verdictEl = document.getElementById('verdictText');

        scoreEl.innerText = trustScore + "%";
        
        // Color Logic
        if(data.verdict === "Suspicious") {
            verdictEl.innerText = "Suspicious Product ⚠️";
            verdictEl.style.color = "#ff4444";
            scoreEl.style.color = "#ff4444";
            document.querySelector('.score-circle').style.borderColor = "#ff4444";
        } else if (data.verdict === "No Reviews Found") {
            verdictEl.innerText = "No Data Found";
            verdictEl.style.color = "#888";
        } else {
            verdictEl.innerText = "Safe to Buy ✅";
            verdictEl.style.color = "#00ffcc";
            scoreEl.style.color = "#00ffcc";
            document.querySelector('.score-circle').style.borderColor = "#00ffcc";
        }
    })
    .catch(error => {
        console.error('Error:', error);
        loading.classList.add('hidden');
        alert("Something went wrong. Please try again.");
    });
}