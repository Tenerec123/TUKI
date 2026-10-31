const PROVIDERS = {
    openrouter: {
        label: 'OpenRouter',
        textUrl: 'https://openrouter.ai/api/v1/models',
        transcriptionUrl: 'https://openrouter.ai/api/v1/models?output_modalities=transcription',
        models: { text: [], transcription: [] },
    },
    'opencode-go': {
        label: 'OpenCode Go',
        modelsUrl: 'https://opencode.ai/inference/go/v1/models',
        models: { text: [], transcription: [] },
    },
    deepgram: {
        label: 'Deepgram',
        models: {
            text: [],
            transcription: [{
                id: 'nova-2',
                name: 'Nova-2',
                provider: 'Deepgram',
                price: null,
                searchText: 'nova-2 deepgram',
            }],
        },
    },
};

// OpenCode Go publishes several API families from its model endpoint. TUKI's
// current agent uses Chat Completions, so only list models served on that API.
const OPENCODE_GO_CHAT_MODELS = new Set([
    'glm-5.3-flash', 'glm-5.3', 'glm-5.2',
    'kimi-k3.1-pro', 'kimi-k3', 'kimi-k2.7-code',
    'longcat-2.0', 'deepseek-v4.1-flash', 'deepseek-v4-pro',
    'mimo-v2.6-flash', 'mimo-v2.6-pro', 'mimo-v2.5', 'mimo-v2.5-pro',
    'minimax-m3.1-flash', 'hy4-preview', 'hy3', 'step-5-preview', 'beam-free',
]);

const keyStatuses = Object.fromEntries(
    Object.keys(PROVIDERS).map(provider => [provider, false]),
);
let capabilities = { llm: false, stt: false, tts: false, chat: false, microphone: false, wake: false };

function renderAvailability() {
    const messages = [];
    if (!capabilities.chat) messages.push('Chat unavailable: configure an LLM provider key.');
    if (!capabilities.microphone) messages.push('Microphone unavailable: configure an LLM and STT provider key.');
    if (!capabilities.wake) messages.push('Wake unavailable: configure LLM, STT, and TTS provider keys.');
    const notice = document.getElementById('llms-availability');
    if (notice) notice.textContent = messages.length ? messages.join(' ') : 'Chat, microphone, and wake are available.';
}

function updateProviderSelects() {
    document.querySelectorAll('.llms-provider-select option').forEach(option => {
        option.disabled = !keyStatuses[option.value];
    });
    const tts = document.getElementById('llms-tts-provider');
    if (tts) {
        tts.querySelectorAll('option').forEach(option => {
            option.disabled = !keyStatuses[option.value];
        });
        if (tts.selectedOptions[0]?.disabled) {
            const available = [...tts.options].find(option => !option.disabled);
            tts.value = available ? available.value : '';
        }
    }
}

function formatPrice(value) {
    const price = Number.parseFloat(value);
    if (!Number.isFinite(price)) return null;
    return `$${(price * 1_000_000).toFixed(2)} / 1M tokens`;
}

function normalizeOpenRouterModel(model) {
    const id = model.id || '';
    const provider = id.includes('/') ? id.split('/')[0] : 'OpenRouter';
    const promptPrice = formatPrice(model.pricing?.prompt);
    const completionPrice = formatPrice(model.pricing?.completion);
    const price = promptPrice && completionPrice
        ? `Input ${promptPrice} · Output ${completionPrice}`
        : null;

    return {
        id,
        name: model.name || id,
        provider,
        price,
        searchText: `${id} ${model.name || ''} ${provider}`.toLowerCase(),
        reasoning: model.reasoning || null,
    };
}

function normalizeOpenCodeGoModel(model) {
    const id = typeof model === 'string' ? model : (model.id || '');
    const name = typeof model === 'string' ? model : (model.name || id);

    return {
        id,
        name,
        provider: 'OpenCode Go',
        price: null,
        searchText: `${id} ${name} OpenCode Go`.toLowerCase(),
        reasoning: typeof model === 'object' ? (model.reasoning || null) : null,
    };
}

async function fetchJSON(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(payload.detail || payload.error?.message || `Request failed (${response.status})`);
    }
    return payload;
}

async function fetchCatalog(url) {
    const payload = await fetchJSON(url);
    const models = payload.data || payload.models || [];
    if (!Array.isArray(models)) throw new Error('Unexpected model catalog response');
    return models;
}

function updateProviderStatuses(statuses) {
    statuses.forEach(({ provider, configured }) => {
        if (!(provider in keyStatuses)) return;
        keyStatuses[provider] = Boolean(configured);
        const card = document.querySelector(`[data-provider-card="${provider}"]`);
        if (card) {
            card.querySelector('.llms-connection-state').textContent = configured
                ? 'Key saved'
                : 'Not connected';
            const deleteButton = card.querySelector('.llms-key-delete');
            if (deleteButton) deleteButton.disabled = !configured;
        }
    });
    updateProviderSelects();
}

async function loadKeyStatuses() {
    const statuses = await fetchJSON('/api/config/keys');
    updateProviderStatuses(statuses);
    capabilities = await fetchJSON('/api/config/capabilities');
    renderAvailability();
}

async function loadTtsProvider() {
    const setting = await fetchJSON('/api/config/tts-provider');
    const select = document.getElementById('llms-tts-provider');
    if (select && keyStatuses[setting.provider]) select.value = setting.provider;
}

async function loadCatalogs() {
    const status = document.getElementById('llms-catalog-status');
    const requests = [];

    if (keyStatuses.openrouter) {
        requests.push(
            fetchCatalog(PROVIDERS.openrouter.textUrl).then(models => {
                PROVIDERS.openrouter.models.text = models
                    .filter(model => (model.architecture?.output_modalities || []).includes('text'))
                    .map(normalizeOpenRouterModel);
            }),
            fetchCatalog(PROVIDERS.openrouter.transcriptionUrl).then(models => {
                PROVIDERS.openrouter.models.transcription = models
                    .map(normalizeOpenRouterModel);
            }),
        );
    }
    if (keyStatuses['opencode-go']) {
        requests.push(
            fetchCatalog(PROVIDERS['opencode-go'].modelsUrl).then(models => {
                PROVIDERS['opencode-go'].models.text = models
                    .map(normalizeOpenCodeGoModel)
                    .filter(model => model.id && OPENCODE_GO_CHAT_MODELS.has(model.id));
            }),
        );
    }

    const results = await Promise.allSettled(requests);
    document.querySelectorAll('.llms-role-card').forEach(card => {
        const providerId = card.dataset.selectedProvider
            || card.querySelector('.llms-provider-select').value;
        const selectedModel = roleCatalog(card.dataset.role, providerId).find(
            model => model.id === card.dataset.selectedModel,
        );
        if (selectedModel) setReasoningEffortOptions(card, selectedModel);
        renderRoleModels(card);
    });

    const failed = results.filter(result => result.status === 'rejected').length;
    const configuredCount = Object.values(keyStatuses).filter(Boolean).length;
    if (configuredCount === 0) {
        status.textContent = 'Save a provider API key to load its model catalog';
    } else if (failed === 0) {
        status.textContent = 'Catalogs loaded for configured providers';
    } else {
        status.textContent = `Some catalogs could not be loaded (${failed} request${failed === 1 ? '' : 's'} failed)`;
    }

    document.querySelectorAll('.llms-role-card').forEach(renderRoleModels);
}

function roleCatalog(role, providerId) {
    const provider = PROVIDERS[providerId];
    if (!provider || !keyStatuses[providerId]) return [];
    if (role === 'stt') return provider.models.transcription;
    return provider.models.text;
}

function setReasoningEffortOptions(card, model) {
    const select = card.querySelector('.llms-effort-select');
    if (!select) return;

    const previousEffort = card.dataset.selectedEffort || '';
    const efforts = model
        ? TukiReasoningEfforts.getReasoningEfforts(model.reasoning)
        : [];
    select.replaceChildren();

    if (!model || efforts.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = model
            ? 'Provider default (no selectable efforts)'
            : 'Effort options unavailable';
        select.appendChild(option);
        select.disabled = true;
        if (model) card.dataset.selectedEffort = '';
        return;
    }

    efforts.forEach(effort => {
        const option = document.createElement('option');
        option.value = effort;
        option.textContent = TukiReasoningEfforts.getReasoningEffortLabel(effort);
        select.appendChild(option);
    });
    select.disabled = false;

    const selectedEffort = efforts.includes(previousEffort)
        ? previousEffort
        : TukiReasoningEfforts.getDefaultReasoningEffort(model.reasoning);
    select.value = selectedEffort;
    card.dataset.selectedEffort = selectedEffort;

    if (previousEffort !== selectedEffort) {
        document.getElementById('llms-save-status').textContent =
            `The least intensive supported reasoning effort is selected for ${model.id}. Save to apply it.`;
    }
}

function renderRoleModels(card) {
    const role = card.dataset.role;
    const providerId = card.querySelector('.llms-provider-select').value;
    const query = card.querySelector('.llms-model-search').value.trim().toLowerCase();
    const list = card.querySelector('.llms-model-list');
    const models = roleCatalog(role, providerId)
        .filter(model => !query || model.searchText.includes(query));

    list.replaceChildren();
    if (models.length === 0) {
        const empty = document.createElement('li');
        empty.className = 'llms-model-empty';
        if (!keyStatuses[providerId]) {
            empty.textContent = 'Save this provider API key to load available models.';
        } else if (role === 'stt' && providerId === 'opencode-go') {
            empty.textContent = 'OpenCode Go does not currently publish transcription models.';
        } else if (role === 'stt' && providerId === 'deepgram') {
            empty.textContent = 'No Deepgram transcription models are available.';
        } else {
            empty.textContent = 'No models match this search, or the catalog is unavailable.';
        }
        list.appendChild(empty);
        return;
    }

    const currentSelection = card.dataset.selectedModel;
    models.forEach(model => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'llms-model-option';
        if (model.id === currentSelection) button.classList.add('is-selected');
        button.dataset.modelId = model.id;

        const title = document.createElement('span');
        title.className = 'llms-model-name';
        title.textContent = model.name;

        const details = document.createElement('span');
        details.className = 'llms-model-meta';
        details.textContent = [model.provider, model.id, model.price].filter(Boolean).join(' · ');

        button.append(title, details);
        button.addEventListener('click', () => selectModel(card, model));
        item.appendChild(button);
        list.appendChild(item);
    });
}

function selectModel(card, model) {
    card.dataset.selectedModel = model.id;
    card.dataset.selectedProvider = card.querySelector('.llms-provider-select').value;

    const selection = card.querySelector('.llms-current-selection');
    selection.replaceChildren();
    const name = document.createElement('strong');
    name.textContent = model.name;
    const metadata = document.createElement('span');
    metadata.textContent = `${model.provider} · ${model.id}`;
    selection.append(name, metadata);
    card.dataset.selectedEffort = '';
    setReasoningEffortOptions(card, model);
    renderRoleModels(card);

    document.getElementById('llms-save-status').textContent =
        'Model selection changed; save to apply it to TUKI.';
}

function applyModelConfig(rows) {
    rows.forEach(([usage, provider, modelName, thinkingProcess]) => {
        const card = document.querySelector(`.llms-role-card[data-role="${usage}"]`);
        if (!card) return;
        const providerSelect = card.querySelector('.llms-provider-select');
        providerSelect.value = provider;
        card.dataset.selectedProvider = provider;
        card.dataset.selectedModel = modelName;

        card.dataset.selectedEffort = thinkingProcess || '';

        const selection = card.querySelector('.llms-current-selection');
        selection.replaceChildren();
        const name = document.createElement('strong');
        name.textContent = modelName;
        const providerLabel = provider === 'opencode-go'
            ? 'OpenCode Go'
            : provider === 'openrouter'
                ? 'OpenRouter'
                : 'Deepgram';
        const metadata = document.createElement('span');
        metadata.textContent = `${providerLabel} · ${modelName}`;
        selection.append(name, metadata);
    });
    document.querySelectorAll('.llms-role-card').forEach(renderRoleModels);
}

async function loadModelConfig() {
    const rows = await fetchJSON('/api/config/models');
    applyModelConfig(rows);
}

document.querySelectorAll('.llms-provider-select').forEach(select => {
    select.addEventListener('change', () => {
        const card = select.closest('.llms-role-card');
        delete card.dataset.selectedModel;
        card.dataset.selectedProvider = select.value;
        card.dataset.selectedEffort = '';
        card.querySelector('.llms-current-selection').textContent = 'No model selected';
        setReasoningEffortOptions(card, null);
        renderRoleModels(card);
    });
});

document.querySelectorAll('.llms-model-search').forEach(input => {
    input.addEventListener('input', () => renderRoleModels(input.closest('.llms-role-card')));
});

document.querySelectorAll('.llms-effort-select').forEach(select => {
    select.addEventListener('change', () => {
        const card = select.closest('.llms-role-card');
        card.dataset.selectedEffort = select.value;
        document.getElementById('llms-save-status').textContent =
            'Reasoning effort changed; save to apply it to TUKI.';
    });
});

document.querySelectorAll('.llms-key-form').forEach(form => {
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const provider = form.dataset.providerKeyForm;
        const input = form.querySelector('input[name="api_key"]');
        const status = form.querySelector('.llms-form-status');
        const key = input.value;
        if (!key.trim()) return;

        status.textContent = 'Saving key to the local TUKI server…';
        try {
            await fetchJSON(`/api/config/keys/${encodeURIComponent(provider)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key }),
            });
            keyStatuses[provider] = true;
            updateProviderStatuses([{ provider, configured: true }]);
            capabilities = await fetchJSON('/api/config/capabilities');
            renderAvailability();
            status.textContent = 'Key saved on the server. Its value cannot be read back here.';
            await loadCatalogs();
        } catch (error) {
            status.textContent = `Could not save key: ${error.message}`;
        } finally {
            input.value = '';
        }
    });
});

document.querySelectorAll('.llms-key-delete').forEach(button => {
    button.addEventListener('click', async () => {
        const provider = button.dataset.providerKeyDelete;
        const card = button.closest('.llms-provider-card');
        const status = card.querySelector('.llms-form-status');
        if (!keyStatuses[provider]) return;
        if (!window.confirm(`Delete the saved ${PROVIDERS[provider].label} key from TUKI?`)) return;

        button.disabled = true;
        status.textContent = 'Deleting saved key…';
        try {
            await fetchJSON(`/api/config/keys/${encodeURIComponent(provider)}`, {
                method: 'DELETE',
            });
            keyStatuses[provider] = false;
            updateProviderStatuses([{ provider, configured: false }]);
            status.textContent = 'Saved key deleted from the server.';
            try {
                await loadKeyStatuses();
                await loadTtsProvider();
                updateProviderSelects();
                await loadCatalogs();
            } catch (refreshError) {
                status.textContent = `Key deleted, but service status could not refresh: ${refreshError.message}`;
            }
        } catch (error) {
            status.textContent = `Could not delete key: ${error.message}`;
            button.disabled = !keyStatuses[provider];
        }
    });
});

document.getElementById('save-llms-config').addEventListener('click', async () => {
    const status = document.getElementById('llms-save-status');
    const rows = [];
    document.querySelectorAll('.llms-role-card').forEach(card => {
        const usage = card.dataset.role;
        const provider = card.dataset.selectedProvider || card.querySelector('.llms-provider-select').value;
        const modelName = card.dataset.selectedModel;
        if (!modelName) return;

        const effortSelect = card.querySelector('.llms-effort-select');
        const thinkingProcess = effortSelect && !effortSelect.disabled
            ? (effortSelect.value || null)
            : (card.dataset.selectedEffort || null);
        rows.push([usage, provider, modelName, thinkingProcess]);
    });

    if (rows.length === 0) {
        status.textContent = 'Select at least one model before saving.';
        return;
    }

    status.textContent = 'Saving model assignments…';
    try {
        const savedRows = await fetchJSON('/api/config/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(rows),
        });
        applyModelConfig(savedRows);
        capabilities = await fetchJSON('/api/config/capabilities');
        renderAvailability();
        const tts = document.getElementById('llms-tts-provider');
        if (tts?.value) {
            await fetchJSON('/api/config/tts-provider', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider: tts.value }),
            });
        }
        status.textContent = 'Model assignments saved and active for new requests.';
    } catch (error) {
        status.textContent = `Could not save model assignments: ${error.message}`;
    }
});

async function initializeLLMSPage() {
    const status = document.getElementById('llms-catalog-status');
    try {
        await Promise.all([loadKeyStatuses(), loadModelConfig()]);
        updateProviderSelects();
        await loadTtsProvider();
        updateProviderSelects();
        await loadCatalogs();
    } catch (error) {
        console.error('[LLMS] Failed to initialize settings:', error);
        status.textContent = `Could not load LLMS settings: ${error.message}`;
    }
}

initializeLLMSPage();
