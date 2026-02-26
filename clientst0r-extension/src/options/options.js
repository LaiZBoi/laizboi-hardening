const serverUrlInput = document.getElementById('serverUrl');
const apiKeyInput    = document.getElementById('apiKey');
const saveBtn        = document.getElementById('saveBtn');
const testBtn        = document.getElementById('testBtn');
const statusEl       = document.getElementById('status');

function showStatus(msg, type) {
  statusEl.textContent = msg;
  statusEl.className = type;
}

// Load saved settings
chrome.storage.local.get(['serverUrl', 'apiKey'], ({ serverUrl, apiKey }) => {
  if (serverUrl) serverUrlInput.value = serverUrl;
  if (apiKey)    apiKeyInput.value    = apiKey;
});

saveBtn.addEventListener('click', () => {
  const serverUrl = serverUrlInput.value.trim().replace(/\/$/, '');
  const apiKey    = apiKeyInput.value.trim();

  if (!serverUrl) { showStatus('Please enter a server URL.', 'err'); return; }
  if (!apiKey)    { showStatus('Please enter an API key.', 'err'); return; }
  if (serverUrl.startsWith('http://') && !serverUrl.includes('localhost') && !serverUrl.match(/^http:\/\/\d+\.\d+\.\d+\.\d+/)) {
    showStatus('Warning: HTTP URLs are insecure. Use HTTPS for production servers.', 'info');
  }

  chrome.storage.local.set({ serverUrl, apiKey }, () => {
    showStatus('Settings saved!', 'ok');
  });
});

testBtn.addEventListener('click', async () => {
  const serverUrl = serverUrlInput.value.trim().replace(/\/$/, '');
  const apiKey    = apiKeyInput.value.trim();

  if (!serverUrl || !apiKey) {
    showStatus('Enter server URL and API key first.', 'err');
    return;
  }

  showStatus('Testing connection…', 'info');
  testBtn.disabled = true;

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'testConnection',
      payload: { serverUrl, apiKey },
    });
    if (response.error) throw new Error(response.error);
    const orgNames = (response.orgs || []).map((o) => o.name).join(', ') || 'No organizations';
    showStatus(`Connected! Organizations: ${orgNames}`, 'ok');
  } catch (err) {
    showStatus(`Connection failed: ${err.message}`, 'err');
  } finally {
    testBtn.disabled = false;
  }
});
