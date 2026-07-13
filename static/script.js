async function analyze() {
    const urlInput = document.getElementById('urlInput');
    const btn = document.getElementById('scanBtn');
    const loader = document.getElementById('btnLoader');
    const resultBox = document.getElementById('result');
    const url = urlInput.value.trim();

    if (!url) {
        alert("Please paste a product URL first.");
        return;
    }

    btn.disabled = true;
    btn.innerText = "Scanning...";
    loader.classList.remove('hidden');
    resultBox.classList.add('hidden');

    try {
        const response = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        const data = await response.json();

        // Invalid URL, unsupported website, no reviews, or AI failure:
        // do not show a score.
        if (!response.ok) {
            throw new Error(data.error || "Unable to analyse this URL.");
        }

        document.getElementById('totalReviews').innerText = data.total;
        document.getElementById('realCount').innerText = data.real;
        document.getElementById('fakeCount').innerText = data.fake;

        const scoreText = document.getElementById('trustScore');
        const badge = document.getElementById('verdictBadge');
        const progressBar = document.getElementById('scoreBar');

        scoreText.innerText = data.score;
        badge.innerText = data.verdict;
        badge.className = 'verdict-badge';

        if (data.score >= 80) {
            badge.classList.add('badge-safe');
            scoreText.style.color = '#059669';
            progressBar.style.backgroundColor = '#059669';
        } else if (data.score >= 50) {
            badge.classList.add('badge-warn');
            scoreText.style.color = '#d97706';
            progressBar.style.backgroundColor = '#d97706';
        } else {
            badge.classList.add('badge-risk');
            scoreText.style.color = '#dc2626';
            progressBar.style.backgroundColor = '#dc2626';
        }

        resultBox.classList.remove('hidden');
        progressBar.style.width = '0%';
        setTimeout(() => {
            progressBar.style.width = `${data.score}%`;
        }, 100);

    } catch (error) {
        console.error("Analysis error:", error);
        resultBox.classList.add('hidden');
        alert(error.message);
    } finally {
        btn.disabled = false;
        btn.innerText = "Analyze";
        loader.classList.add('hidden');
    }
}
