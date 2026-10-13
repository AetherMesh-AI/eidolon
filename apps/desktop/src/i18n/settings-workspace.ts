/** Source copy; unreviewed translations use the catalog's English fallback. */
export const settingsWorkspaceEn = {
  heading: 'Settings',
  overview: 'Overview',
  modelParameters: 'Context and fallback models',
  openSection: 'Open {section}',
  introduction: 'Configure this desktop and its connected runtime.',
  saveNote: 'Some changes save automatically. Use each page’s Save or confirmation controls where shown.',
  sections: {
    overview: 'Model, notifications and configuration in one workspace',
    'config:model': 'Models, providers and response defaults',
    'config:chat': 'Conversation behavior and presentation',
    'config:appearance': 'Language, themes and desktop appearance',
    'config:workspace': 'Projects, execution and workspace defaults',
    'config:safety': 'Approval and protection settings',
    'config:browser': 'Browser access and session preferences',
    'config:memory': 'Memory storage and retrieval',
    'config:voice': 'Desktop speech and audio preferences',
    'config:advanced': 'Device behavior and advanced configuration',
    providers: 'Accounts, API keys and custom endpoints',
    gateway: 'Local, remote, SSH and cloud connections',
    keybinds: 'Keyboard navigation and shortcuts',
    keys: 'Tool configuration and credentials',
    notifications: 'Device alerts and notification preferences',
    billing: 'Available plans and billing controls',
    plugins: 'Installed desktop and runtime extensions',
    sessions: 'Review and restore archived conversations',
    about: 'Application information and updates'
  }
}

export const modelParametersByLocale = {
  en: settingsWorkspaceEn.modelParameters,
  ja: 'コンテキストとフォールバックモデル',
  zh: '上下文与备用模型',
  'zh-hant': '上下文與備用模型',
  ar: 'السياق والنماذج البديلة',
  ru: 'Контекст и резервные модели'
}
