(function (root) {
    const GATEWAY_EFFORTS_DESCENDING = [
        'max', 'xhigh', 'high', 'medium', 'low', 'minimal', 'none',
    ];
    const EFFORT_LABELS = {
        none: 'No reasoning',
        minimal: 'Minimal',
        low: 'Low',
        medium: 'Medium',
        high: 'High',
        xhigh: 'Extra high',
        max: 'Maximum',
    };

    // OpenRouter lists supported efforts from highest to lowest. A null
    // allowlist means every gateway effort is accepted; a missing list means
    // the model does not expose effort selection.
    function getReasoningEfforts(reasoning) {
        if (!reasoning || typeof reasoning !== 'object') return [];

        let efforts;
        if (Array.isArray(reasoning.supported_efforts)) {
            efforts = reasoning.supported_efforts;
        } else if (reasoning.supported_efforts === null) {
            efforts = GATEWAY_EFFORTS_DESCENDING;
        } else {
            return [];
        }

        return efforts.filter(effort =>
            typeof effort === 'string'
            && (!reasoning.mandatory || effort !== 'none')
        );
    }

    function getDefaultReasoningEffort(reasoning) {
        const efforts = getReasoningEfforts(reasoning);
        return efforts.length ? efforts[efforts.length - 1] : null;
    }

    function getReasoningEffortLabel(effort) {
        return EFFORT_LABELS[effort] || effort;
    }

    const api = {
        getReasoningEfforts,
        getDefaultReasoningEffort,
        getReasoningEffortLabel,
    };
    root.TukiReasoningEfforts = api;
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(globalThis);
