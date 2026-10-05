/** The real OpenAI-compatible client may omit stream for ordinary completions. */
export function projectProviderResponse(
  message: { role: string; content: string | null; tool_calls?: unknown[] },
  sequence: number,
  stream?: boolean
) {
  if (stream !== undefined && stream !== false) {
    throw new Error('The project fixture requires a non-streaming completion')
  }
  return {
    id: `project-fixture-${sequence}`,
    object: 'chat.completion',
    created: 1,
    model: 'mock-model',
    choices: [{ index: 0, message, finish_reason: 'tool_calls' in message ? 'tool_calls' : 'stop' }],
    usage: { prompt_tokens: 30, completion_tokens: 20, total_tokens: 50 }
  }
}
