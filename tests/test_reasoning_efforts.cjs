const assert = require('node:assert/strict');
const test = require('node:test');

const {
    getReasoningEfforts,
    getDefaultReasoningEffort,
} = require('../frontend/llms_reasoning.js');

test('offers only supported model efforts and defaults to the least intensive', () => {
    const reasoning = {
        mandatory: true,
        supported_efforts: ['high', 'medium', 'low', 'minimal', 'none'],
    };

    assert.deepEqual(getReasoningEfforts(reasoning), [
        'high', 'medium', 'low', 'minimal',
    ]);
    assert.equal(getDefaultReasoningEffort(reasoning), 'minimal');
});

test('offers none when reasoning is optional and the model supports it', () => {
    const reasoning = {
        mandatory: false,
        supported_efforts: ['high', 'medium', 'low', 'minimal', 'none'],
    };

    assert.deepEqual(getReasoningEfforts(reasoning), [
        'high', 'medium', 'low', 'minimal', 'none',
    ]);
    assert.equal(getDefaultReasoningEffort(reasoning), 'none');
});
