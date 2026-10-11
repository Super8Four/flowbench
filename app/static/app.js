const statusLabel = document.querySelector('#database');
const refreshButton = document.querySelector('#refresh');

async function checkConnection() {
  refreshButton.disabled = true;
  statusLabel.textContent = 'Checking…';
  try {
    const response = await fetch('/api/health');
    const data = await response.json();
    statusLabel.textContent = data.database === 'connected' ? 'Connected' : 'Unavailable';
  } catch {
    statusLabel.textContent = 'Unable to reach server';
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener('click', checkConnection);
checkConnection();
