const screen = document.querySelector('#screen');
const ctx = screen.getContext('2d');
const sessionLabel = document.querySelector('#session-value');
const connectionLabel = document.querySelector('#connection-label');
const runtimeChip = document.querySelector('#runtime-chip');
let sessionId = null;
let latestState = null;
// Per-button long-press tracking so several keys can be held at once
// (mouse + keyboard, or multi-touch) without clobbering each other.
const LONG_PRESS_MS = 850;
const holdState = new Map();
// Audio bridge: speaker playback of firmware PCM frames + microphone capture
// forwarded to the firmware backend. Both stay inert until the backend talks
// the audio JSON-lines protocol -- no synthesized or fake audio ever plays.
// Speaker playback pulls frames from /api/firmware/<id>/audio (a lossless
// queue) instead of SSE snapshots: the snapshot stream only carries the
// newest frame, so frames produced between two pushes were dropped and the
// playback crackled.  The emulated I2S also produces audio a few percent
// slower/faster than wall time (virtual clock drift), so playback runs a
// gentle rate controller: it keeps ~280ms of audio scheduled ahead and
// micro-adjusts playbackRate (clamped to 0.90..1.08) instead of periodically
// jumping the timeline -- jumps click audibly on continuous music.  When the
// device itself stalls (firmware decode hiccups), the seam is faded in/out
// over 6ms so the unavoidable content gap sounds like a soft dip, not a click.
const SPEAKER_LEAD_TARGET = 0.28;
const SPEAKER_LEAD_GAIN = 0.35;
const SPEAKER_ADJ_MIN = 0.90;
const SPEAKER_ADJ_MAX = 1.08;
const SPEAKER_LEAD_FLOOR = 0.05;
const SPEAKER_LEAD_RESET = 0.15;
const SPEAKER_SEAM_FADE = 0.006;
const speaker = {
  enabled: false,
  ctx: null,
  nextTime: 0,
  pumpTimer: 0,
  consumed: -1,
  consumedArtifact: null,
  lastSourceGain: null,
  lastSourceEnd: 0,
};
const mic = { enabled: false, requesting: false, ctx: null, stream: null, source: null, processor: null, sink: null };
let lastAudioRevision = -1;
let selectedFirmwareId = null;
let firmwarePollTimer = null;
let pendingNativeFrame = null;
let nativeFrameRaf = 0;
let lastDrawnFrameKey = null;
let lastTelemetryRevision = null;
let hasNativeFrame = false;
let playCatalog = [];
let selectedPlaySlug = null;
let playCatalogPreviousFocus = null;
let playCatalogCloseTimer = null;
let lastFirmwarePanel = null;
let lastFirmwareFileName = null;
let runtimeOnline = false;

// ---- UI language (i18n) -------------------------------------------------
// The toggle switches simulator chrome only. Firmware-rendered screen
// content is never touched: the device screen stays the firmware's output.
const I18N = {
  en: {
    'app.title': 'AI Passport // Simulator Deck',
    'conn.booting': 'BOOTING RUNTIME',
    'conn.online': 'RUNTIME ONLINE',
    'conn.reconnecting': 'RECONNECTING',
    'conn.offline': 'RUNTIME OFFLINE',
    'chip.firmwareOnly': 'FIRMWARE ONLY',
    'chip.starting': 'STARTING',
    'chip.linkLost': 'LINK LOST',
    'chip.error': 'ERROR',
    'catalog.launch': '玩法目录',
    'catalog.launchSub': 'OFFICIAL FIRMWARE',
    'site.official': 'OFFICIAL SITE',
    'device.label': 'DEVICE 01',
    'keys.virtual': 'VIRTUAL KEYS',
    'deck.eyebrow': 'CONTROL DECK',
    'deck.title': 'Runtime telemetry',
    'label.currentView': 'CURRENT VIEW',
    'label.screenSource': 'SCREEN SOURCE',
    'label.realOutput': 'REAL FIRMWARE OUTPUT',
    'label.inputEvents': 'INPUT EVENTS',
    'view.firmwareConsole': 'FIRMWARE CONSOLE',
    'revision.prefix': 'REVISION',
    'revision.dash': 'REVISION —',
    'bridge.unavailable': 'UNAVAILABLE',
    'logs.waiting': 'waiting for worker telemetry…',
    'worker.isolated': 'ISOLATED WORKER',
    'session.prefix': 'SESSION',
    'session.dash': 'SESSION —',
    'status.notLoaded': 'NOT LOADED',
    'status.ready': 'READY',
    'status.running': 'RUNNING',
    'status.unavailable': 'UNAVAILABLE',
    'status.error': 'ERROR',
    'status.exited': 'EXITED',
    'status.notRunning': 'NOT RUNNING',
    'status.unknown': 'UNKNOWN',
    'fw.lab': 'FIRMWARE LAB',
    'fw.labSub': 'ONLY SOURCE OF APP BEHAVIOR',
    'fw.chooseBin': 'CHOOSE .BIN IMAGE',
    'fw.loadImage': 'LOAD IMAGE',
    'fw.notice': 'Upload an ESP-IDF .bin image to parse its image header.',
    'fw.select.empty': 'NO VALIDATED IMAGES',
    'label.chipFormat': 'CHIP / FORMAT',
    'label.entry': 'ENTRY ADDRESS',
    'label.partitions': 'PARTITIONS',
    'label.source': 'SOURCE',
    'seg.count': '{chip} / {count} SEG',
    'partitions.noTable': 'APP IMAGE / NO TABLE',
    'source.localUpload': 'LOCAL UPLOAD',
    'source.official': 'OFFICIAL',
    'source.community': 'COMMUNITY',
    'fw.analyze': 'ANALYZE',
    'fw.analyzeSub': 'VERIFY IMAGE',
    'fw.run': 'RUN FIRMWARE',
    'fw.runSub': 'ESP32-C3 BACKEND',
    'fw.stop': 'STOP EXECUTION',
    'fw.stopSub': 'TERMINATE QEMU',
    'label.execution': 'EXECUTION',
    'exec.reasonUnknown': 'QEMU status unknown',
    'fw.logEmpty': 'Firmware output will appear here.',
    'panel.loadFirst': 'Load a validated image first',
    'panel.qemuDetected': 'QEMU backend detected',
    'panel.qemuMissing': 'QEMU backend not detected',
    'panel.noOutput': 'No firmware output.',
    'fwStatus.validated': 'LOADED / VALIDATED',
    'fwStatus.uploaded': 'UPLOADED',
    'fwStatus.invalid': 'INVALID IMAGE',
    'fwStatus.running': 'EXECUTION RUNNING',
    'fwStatus.unavailable': 'EXECUTION UNAVAILABLE',
    'fwStatus.exited': 'EXECUTION EXITED',
    'fwStatus.error': 'EXECUTION ERROR',
    'fwStatus.notRunning': 'NOT RUNNING',
    'bus.input': 'INPUT BUS',
    'key.up': 'UP',
    'key.ok': 'OK',
    'key.down': 'DOWN',
    'bus.audio': 'AUDIO BUS',
    'audio.speaker': 'SPEAKER',
    'audio.mic': 'MICROPHONE',
    'state.on': 'ON',
    'state.off': 'OFF',
    'state.live': 'LIVE',
    'audio.statusIdle': 'Speaker and microphone stay off; turn on SPEAKER first.',
    'audio.noBridge': 'SPK {o} frames · MIC {i} frames · The firmware backend provides no audio bridge; the speaker only plays real {"type":"audio"} PCM frames. No fake audio.',
    'audio.notRunning': 'SPK {o} frames · MIC {i} frames · Speaker standby: the firmware is not executing. Press RUN FIRMWARE above to start audio.',
    'audio.error': 'Speaker playback error: {message}. The playback timeline was reset; if it persists, refresh the page.',
    'audio.ready': 'SPK {o} frames · MIC {i} frames · Speaker ready: firmware PCM frames play in real time; with MICROPHONE on, captured audio is forwarded to the firmware.',
    'audio.unsupported': 'This browser does not support microphone capture (getUserMedia + secure context required).',
    'audio.requesting': 'Requesting microphone permission…',
    'audio.unavailable': 'Microphone unavailable: {message}',
    'audio.live': 'Microphone live: 16 kHz mono PCM is being forwarded to the firmware backend.',
    'audio.speakerOn': 'Speaker on: firmware PCM frames play in real time.',
    'audio.speakerOff': 'Speaker off.',
    'audio.micStopped': 'Microphone stopped.',
    'stream.title': 'EVENT STREAM',
    'device.reset': 'RESET DEVICE',
    'catalog.eyebrow': 'OFFICIAL PLAY CATALOG / {count} PROJECTS',
    'catalog.eyebrowLoading': 'OFFICIAL PLAY CATALOG / LOADING',
    'catalog.source': 'OFFICIAL SOURCE',
    'catalog.playsPage': 'Official plays page ↗',
    'catalog.refresh': 'REFRESH',
    'catalog.loading': 'Loading plays from the official source…',
    'catalog.close': '关闭目录',
    'catalog.searchPlaceholder': '搜索标题、作者或关键词',
    'catalog.allCategories': '全部分类',
    'catalog.count': '{shown} / {total} PLAYS',
    'catalog.noMatch': '没有匹配的玩法。',
    'catalog.unknownAuthor': '未知作者',
    'catalog.uncategorized': '未分类',
    'detail.select': '选择一个玩法',
    'detail.default': '项目说明、固件版本与校验信息会显示在这里。',
    'detail.noDescription': '暂无项目说明。',
    'detail.author': 'AUTHOR',
    'detail.firmware': 'FIRMWARE',
    'detail.format': 'FORMAT',
    'detail.status': 'STATUS',
    'detail.available': '可下载',
    'detail.unavailable': '不可用',
    'playSource.official': '官方项目',
    'playSource.community': '社区项目',
    'play.download': '下载并加载',
    'play.downloadSub': 'VALIDATE + LOAD',
    'play.run': '下载并运行',
    'play.runSub': 'VALIDATE + RUN QEMU',
    'play.github': '打开项目主页 ↗',
    'play.notice': '目录固件只从官方服务器获取。',
    'play.downloading': '正在从官方源下载，并校验 SHA-256 与 ESP32-C3 镜像…',
    'play.loadedOk': '{title} 已验证并加载到模拟器。',
    'play.reusedNote': '已复用本地缓存。',
    'play.downloadFailed': '玩法固件下载失败',
    'play.loadFailed': '官方玩法目录读取失败',
    'screen.noFirmware': 'NO FIRMWARE LOADED',
    'screen.firmwareReady': 'FIRMWARE READY',
    'screen.runningNoDisplay': 'FIRMWARE RUNNING / DISPLAY OUTPUT UNAVAILABLE',
    'screen.loadedNoExecution': 'FIRMWARE LOADED / EXECUTION UNAVAILABLE',
    'screen.executionError': 'FIRMWARE EXECUTION ERROR',
    'screen.executionExited': 'FIRMWARE EXECUTION EXITED',
    'screen.hint': 'LOAD A VALIDATED .BIN IMAGE',
    'screen.defaultReason': 'The simulator will not draw an application without firmware output.',
    'screen.workerFailed': 'The simulator worker could not be started.',
    'screen.console': 'FIRMWARE CONSOLE',
    'screen.displayNote': 'DISPLAY = REAL FIRMWARE FRAMEBUFFER ONLY',
    'screen.noDemo': 'NO BUILT-IN DEMO',
    'reason.exitedWithCode': 'firmware process exited with code {code}',
    'reason.frameDecode': 'frame decode failed: ',
    'reason.exited': 'firmware process exited',
    'upload.chooseFirst': 'Choose an ESP32-C3 .bin image first.',
    'upload.reading': 'Reading image header and verifying checksum…',
    'upload.validationFailed': 'Firmware validation failed.',
    'upload.failed': 'Firmware upload failed.',
    'upload.analysisFailed': 'Firmware analysis failed.',
    'run.startFailed': 'Firmware could not start.',
    'run.requestFailed': 'Firmware execution request failed.',
    'run.stopFailed': 'Firmware stop request failed.',
    'list.unavailable': 'firmware list unavailable',
  },
  zh: {
    'app.title': 'AI 护照 // 模拟器操作台',
    'conn.booting': '运行时启动中',
    'conn.online': '运行时在线',
    'conn.reconnecting': '重连中',
    'conn.offline': '运行时离线',
    'chip.firmwareOnly': '仅固件模式',
    'chip.starting': '启动中',
    'chip.linkLost': '连接丢失',
    'chip.error': '错误',
    'catalog.launch': '玩法目录',
    'catalog.launchSub': '官方固件',
    'site.official': '官方网站',
    'device.label': '设备 01',
    'keys.virtual': '虚拟按键',
    'deck.eyebrow': '控制台',
    'deck.title': '运行时遥测',
    'label.currentView': '当前视图',
    'label.screenSource': '屏幕来源',
    'label.realOutput': '真实固件输出',
    'label.inputEvents': '输入事件',
    'view.firmwareConsole': '固件控制台',
    'revision.prefix': '修订',
    'revision.dash': '修订 —',
    'bridge.unavailable': '不可用',
    'logs.waiting': '等待 worker 遥测…',
    'worker.isolated': '隔离 Worker',
    'session.prefix': '会话',
    'session.dash': '会话 —',
    'status.notLoaded': '未加载',
    'status.ready': '就绪',
    'status.running': '运行中',
    'status.unavailable': '不可用',
    'status.error': '错误',
    'status.exited': '已退出',
    'status.notRunning': '未运行',
    'status.unknown': '未知',
    'fw.lab': '固件实验室',
    'fw.labSub': '应用行为的唯一来源',
    'fw.chooseBin': '选择 .BIN 镜像',
    'fw.loadImage': '加载镜像',
    'fw.notice': '上传 ESP-IDF .bin 镜像以解析其镜像头。',
    'fw.select.empty': '暂无已验证镜像',
    'label.chipFormat': '芯片 / 格式',
    'label.entry': '入口地址',
    'label.partitions': '分区表',
    'label.source': '来源',
    'seg.count': '{chip} / {count} 段',
    'partitions.noTable': '应用镜像 / 无分区表',
    'source.localUpload': '本地上传',
    'source.official': '官方',
    'source.community': '社区',
    'fw.analyze': '解析镜像',
    'fw.analyzeSub': '校验内容',
    'fw.run': '运行固件',
    'fw.runSub': 'ESP32-C3 后端',
    'fw.stop': '停止运行',
    'fw.stopSub': '终止 QEMU',
    'label.execution': '执行状态',
    'exec.reasonUnknown': 'QEMU 状态未知',
    'fw.logEmpty': '固件输出将显示在这里。',
    'panel.loadFirst': '请先加载已验证的镜像',
    'panel.qemuDetected': '已检测到 QEMU 后端',
    'panel.qemuMissing': '未检测到 QEMU 后端',
    'panel.noOutput': '暂无固件输出。',
    'fwStatus.validated': '已加载 / 已验证',
    'fwStatus.uploaded': '已上传',
    'fwStatus.invalid': '无效镜像',
    'fwStatus.running': '执行运行中',
    'fwStatus.unavailable': '执行不可用',
    'fwStatus.exited': '执行已退出',
    'fwStatus.error': '执行错误',
    'fwStatus.notRunning': '未运行',
    'bus.input': '输入总线',
    'key.up': '上',
    'key.ok': '确认',
    'key.down': '下',
    'bus.audio': '音频总线',
    'audio.speaker': '扬声器',
    'audio.mic': '麦克风',
    'state.on': '开',
    'state.off': '关',
    'state.live': '采集中',
    'audio.statusIdle': '扬声器与麦克风均未开启；请先打开「扬声器」。',
    'audio.noBridge': 'SPK {o} 帧 · MIC {i} 帧 · 当前固件后端未提供音频桥；只有后端输出 {"type":"audio"} PCM 帧时扬声器才会有真实声音，不会伪造音频。',
    'audio.notRunning': 'SPK {o} 帧 · MIC {i} 帧 · 扬声器待命：固件未在运行。点击上方"运行固件"即可开始出声。',
    'audio.error': '扬声器播放出错：{message}。播放时间线已重置；若持续出现请刷新页面。',
    'audio.ready': 'SPK {o} 帧 · MIC {i} 帧 · 扬声器就绪：固件输出的 PCM 帧将实时播放；开启麦克风后采集音频将转发给固件。',
    'audio.unsupported': '此浏览器不支持麦克风采集（需要 getUserMedia + 安全上下文）。',
    'audio.requesting': '正在请求麦克风权限…',
    'audio.unavailable': '麦克风不可用：{message}',
    'audio.live': '麦克风采集中：16 kHz 单声道 PCM 正在转发给固件后端。',
    'audio.speakerOn': '扬声器已开启：固件输出的 PCM 帧将实时播放。',
    'audio.speakerOff': '扬声器已关闭。',
    'audio.micStopped': '麦克风已停止。',
    'stream.title': '事件流',
    'device.reset': '重启设备',
    'catalog.eyebrow': '官方玩法目录 / {count} 个项目',
    'catalog.eyebrowLoading': '官方玩法目录 / 加载中',
    'catalog.source': '官方来源',
    'catalog.playsPage': '官方玩法页面 ↗',
    'catalog.refresh': '刷新目录',
    'catalog.loading': '正在从官方源读取玩法目录…',
    'catalog.close': '关闭目录',
    'catalog.searchPlaceholder': '搜索标题、作者或关键词',
    'catalog.allCategories': '全部分类',
    'catalog.count': '{shown} / {total} 个玩法',
    'catalog.noMatch': '没有匹配的玩法。',
    'catalog.unknownAuthor': '未知作者',
    'catalog.uncategorized': '未分类',
    'detail.select': '选择一个玩法',
    'detail.default': '项目说明、固件版本与校验信息会显示在这里。',
    'detail.noDescription': '暂无项目说明。',
    'detail.author': '作者',
    'detail.firmware': '固件',
    'detail.format': '格式',
    'detail.status': '状态',
    'detail.available': '可下载',
    'detail.unavailable': '不可用',
    'playSource.official': '官方项目',
    'playSource.community': '社区项目',
    'play.download': '下载并加载',
    'play.downloadSub': '校验并加载',
    'play.run': '下载并运行',
    'play.runSub': '校验并运行 QEMU',
    'play.github': '打开项目主页 ↗',
    'play.notice': '目录固件只从官方服务器获取。',
    'play.downloading': '正在从官方源下载，并校验 SHA-256 与 ESP32-C3 镜像…',
    'play.loadedOk': '{title} 已验证并加载到模拟器。',
    'play.reusedNote': '已复用本地缓存。',
    'play.downloadFailed': '玩法固件下载失败',
    'play.loadFailed': '官方玩法目录读取失败',
    'screen.noFirmware': '未加载固件',
    'screen.firmwareReady': '固件就绪',
    'screen.runningNoDisplay': '固件运行中 / 显示输出不可用',
    'screen.loadedNoExecution': '固件已加载 / 执行不可用',
    'screen.executionError': '固件执行错误',
    'screen.executionExited': '固件执行已退出',
    'screen.hint': '请加载已验证的 .BIN 镜像',
    'screen.defaultReason': '模拟器不会在没有固件输出时绘制应用界面。',
    'screen.workerFailed': '模拟器 worker 无法启动。',
    'screen.console': '固件控制台',
    'screen.displayNote': '显示 = 仅真实固件帧缓冲',
    'screen.noDemo': '无内置演示',
    'reason.exitedWithCode': '固件进程已退出（退出码 {code}）',
    'reason.frameDecode': '帧解码失败：',
    'reason.exited': '固件进程已退出',
    'upload.chooseFirst': '请先选择 ESP32-C3 .bin 镜像。',
    'upload.reading': '正在读取镜像头并校验校验和…',
    'upload.validationFailed': '固件校验失败。',
    'upload.failed': '固件上传失败。',
    'upload.analysisFailed': '固件解析失败。',
    'run.startFailed': '固件无法启动。',
    'run.requestFailed': '固件运行请求失败。',
    'run.stopFailed': '固件停止请求失败。',
    'list.unavailable': '固件列表不可用',
  },
};

let uiLang = (() => {
  try {
    const stored = localStorage.getItem('sim-lang');
    if (stored === 'zh' || stored === 'en') return stored;
  } catch (error) { /* storage unavailable */ }
  return (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
})();

function t(key, params) {
  const value = I18N[uiLang]?.[key] ?? I18N.en[key];
  const template = value === undefined ? I18N.en[key] ?? key : value;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? `{${name}}`));
}

function applyStaticI18n() {
  document.querySelectorAll('[data-i18n]').forEach((node) => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((node) => { node.placeholder = t(node.dataset.i18nPlaceholder); });
}

function updateLangToggle() {
  const button = document.querySelector('#lang-toggle');
  if (!button) return;
  button.textContent = uiLang === 'zh' ? 'EN' : '中文';
  button.setAttribute('aria-label', uiLang === 'zh' ? 'Switch to English' : '切换到中文');
}

function applyLanguage(lang) {
  uiLang = lang === 'zh' ? 'zh' : 'en';
  try { localStorage.setItem('sim-lang', uiLang); } catch (error) { /* storage unavailable */ }
  document.documentElement.lang = uiLang === 'zh' ? 'zh-CN' : 'en';
  document.title = t('app.title');
  applyStaticI18n();
  updateLangToggle();
  forceUiRefresh();
}

function forceUiRefresh() {
  // Drop render caches so every dynamic string re-renders in the new language.
  lastTelemetryRevision = null;
  lastAudioRevision = -1;
  audioStatusHoldUntil = 0;
  const fileLabel = document.querySelector('#firmware-file-label');
  if (fileLabel && !lastFirmwareFileName) fileLabel.textContent = t('fw.chooseBin');
  // Restore connection/session labels that static i18n attributes reset.
  connectionLabel.textContent = t(runtimeOnline ? 'conn.online' : 'conn.booting');
  runtimeChip.textContent = t(runtimeOnline ? 'chip.firmwareOnly' : 'chip.starting');
  sessionLabel.textContent = sessionId ? `${t('session.prefix')} ${sessionId.toUpperCase()}` : t('session.dash');
  if (latestState) render(latestState);
  if (lastFirmwarePanel) renderFirmwareArtifact(lastFirmwarePanel.artifact, lastFirmwarePanel.run);
  if (playCatalog.length) {
    renderPlayCategories();
    renderPlayList();
    renderPlayDetail(playCatalog.find((play) => play.slug === selectedPlaySlug) || null);
    setPlayNotice(t('play.notice'));
  }
}

function statusLabel(status) {
  const map = {
    'not-loaded': 'status.notLoaded',
    'ready': 'status.ready',
    'running': 'status.running',
    'unavailable': 'status.unavailable',
    'error': 'status.error',
    'exited': 'status.exited',
    'not-running': 'status.notRunning',
  };
  const key = map[status];
  return key ? t(key) : String(status || t('status.unknown'));
}

function knownMapping(mapping, value, fallback = '') {
  const key = mapping[String(value || '')];
  return key ? t(key) : String(value || fallback);
}

const SCREEN_MESSAGES = {
  'NO FIRMWARE LOADED': 'screen.noFirmware',
  'FIRMWARE READY': 'screen.firmwareReady',
  'FIRMWARE RUNNING / DISPLAY OUTPUT UNAVAILABLE': 'screen.runningNoDisplay',
  'FIRMWARE LOADED / EXECUTION UNAVAILABLE': 'screen.loadedNoExecution',
  'FIRMWARE EXECUTION ERROR': 'screen.executionError',
  'FIRMWARE EXECUTION EXITED': 'screen.executionExited',
  'RUNTIME OFFLINE': 'conn.offline',
};

const KNOWN_REASONS = {
  'The simulator will not draw an application without firmware output.': 'screen.defaultReason',
  'The simulator worker could not be started.': 'screen.workerFailed',
  'Load a validated image first': 'panel.loadFirst',
};

function translateReason(reason) {
  const value = String(reason || '');
  const direct = KNOWN_REASONS[value];
  if (direct) return t(direct);
  let match = value.match(/^process exited with code (-?\d+)$/);
  if (match) return t('reason.exitedWithCode', {code: match[1]});
  match = value.match(/^frame decode failed: (.*)$/);
  if (match) return `${t('reason.frameDecode')}${match[1]}`;
  if (value === 'firmware process exited') return t('reason.exited');
  return value;
}

// CJK characters carry double width in the simulator's monospace canvas font,
// so line wrapping is measured in units (latin = 1, CJK = 2).
const CJK_RE = /[\u2e80-\u9fff\uf900-\ufaff\uff01-\uff60\u3000-\u303f]/;

function wrapForCanvas(reason, maxUnits = 56) {
  const lines = [];
  let line = '';
  let width = 0;
  for (const ch of String(reason)) {
    const w = CJK_RE.test(ch) ? 2 : 1;
    if (width + w > maxUnits) {
      lines.push(line);
      line = '';
      width = 0;
      if (ch === ' ') continue;
    }
    line += ch;
    width += w;
  }
  if (line) lines.push(line);
  return lines;
}

const COLORS = {
  ink: '#e7f1e8',
  muted: '#8aa2a0',
  acid: '#c8fa73',
  cyan: '#84dfd1',
  border: '#356e72',
  bg: '#071720',
  warning: '#f0b36b',
  error: '#f0a080',
};

const R5_TO_8 = Uint8Array.from({length: 32}, (_, value) => Math.round(value * 255 / 31));
const G6_TO_8 = Uint8Array.from({length: 64}, (_, value) => Math.round(value * 255 / 63));

function text(value, x, y, size = 10, color = COLORS.ink, align = 'left') {
  ctx.font = `${size}px "Space Mono", monospace`;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = 'middle';
  ctx.fillText(String(value), x, y);
}

function clearScreen() {
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, screen.width, screen.height);
}

function nativeFrameKey(screenState) {
  return `${screenState.frame_revision ?? ''}:${screenState.frame_rgb565_b64 ?? ''}`;
}

function queueNativeFrame(screenState) {
  hasNativeFrame = true;
  pendingNativeFrame = screenState;
  if (!nativeFrameRaf) nativeFrameRaf = window.requestAnimationFrame(flushNativeFrame);
}

function flushNativeFrame() {
  nativeFrameRaf = 0;
  const screenState = pendingNativeFrame;
  pendingNativeFrame = null;
  if (screenState) drawNativeFrame(screenState);
}

function drawNativeFrame(screenState) {
  const width = Number(screenState.width || 240);
  const height = Number(screenState.height || 320);
  const encoded = screenState.frame_rgb565_b64;
  if (!encoded || screenState.format !== 'rgb565-le') {
    drawFirmwareStatus(screenState);
    return;
  }
  try {
    const frameKey = nativeFrameKey(screenState);
    if (frameKey === lastDrawnFrameKey) return;
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    if (bytes.length < width * height * 2) throw new Error('incomplete framebuffer');
    const pixels = new Uint8ClampedArray(width * height * 4);
    for (let index = 0; index < width * height; index += 1) {
      const word = bytes[index * 2] | (bytes[index * 2 + 1] << 8);
      const red = R5_TO_8[(word >> 11) & 0x1f];
      const green = G6_TO_8[(word >> 5) & 0x3f];
      const blue = R5_TO_8[word & 0x1f];
      const offset = index * 4;
      pixels[offset] = red;
      pixels[offset + 1] = green;
      pixels[offset + 2] = blue;
      pixels[offset + 3] = 255;
    }
    ctx.putImageData(new ImageData(pixels, width, height), 0, 0);
    lastDrawnFrameKey = frameKey;
  } catch (error) {
    drawFirmwareStatus({ ...screenState, reason: `frame decode failed: ${error.message}`, status: 'error' });
  }
}

function renderScreen(screenState) {
  if (screenState?.frame_rgb565_b64) {
    queueNativeFrame(screenState);
    return;
  }
  pendingNativeFrame = null;
  hasNativeFrame = false;
  lastDrawnFrameKey = null;
  drawFirmwareStatus(screenState || {});
}

function drawFirmwareStatus(screenState) {
  clearScreen();
  ctx.strokeStyle = 'rgba(132,223,209,.08)';
  for (let x = 0; x < 240; x += 12) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, 320);
    ctx.stroke();
  }
  for (let y = 0; y < 320; y += 12) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(240, y);
    ctx.stroke();
  }
  text(t('screen.console'), 15, 20, 9, COLORS.acid);
  ctx.strokeStyle = COLORS.border;
  ctx.strokeRect(14, 31, 212, 1);

  const status = screenState.status || 'not-loaded';
  const color = status === 'error' ? COLORS.error : status === 'unavailable' ? COLORS.warning : COLORS.acid;
  const message = knownMapping(SCREEN_MESSAGES, screenState.message, t('screen.noFirmware'));
  text(message, 120, 101, 13, color, 'center');
  text(screenState.filename || t('screen.hint'), 120, 139, 8, COLORS.ink, 'center');
  text(statusLabel(screenState.execution_status || 'not-running'), 120, 166, 8, COLORS.cyan, 'center');

  const reason = translateReason(screenState.reason) || t('screen.defaultReason');
  const reasonLines = wrapForCanvas(reason).slice(0, 6);
  let y = 201;
  reasonLines.forEach((lineText) => {
    text(lineText, 120, y, 6, COLORS.muted, 'center');
    y += 13;
  });
  text(t('screen.displayNote'), 120, 291, 6, COLORS.cyan, 'center');
  text(t('screen.noDemo'), 120, 306, 6, COLORS.muted, 'center');
}

function render(state) {
  latestState = state;
  const view = state.screen || {};
  if (view.kind === 'firmware' && view.frame_rgb565_b64) queueNativeFrame(view);
  else renderScreen(view);

  const audio = state.audio || null;
  // Playback is driven by the lossless audio pump (speakerPumpTick), not by
  // SSE snapshots -- snapshots drop frames produced between two pushes.
  lastAudioRevision = audio ? Number(audio.revision) : -1;
  updateAudioPanel(audio, state.firmware || null);

  if (state.revision === lastTelemetryRevision) return;
  lastTelemetryRevision = state.revision;
  document.querySelector('#page-value').textContent = t('view.firmwareConsole');
  document.querySelector('#revision-value').textContent = state.revision === undefined || state.revision === null
    ? t('revision.dash') : `${t('revision.prefix')} ${state.revision}`;
  const firmware = state.firmware || {};
  document.querySelector('#input-count').textContent = `${state.input?.events?.length || 0}`;
  document.querySelector('#input-bridge').textContent = firmware.input_bridge === 'json-lines'
    ? 'JSON-LINES' : t('bridge.unavailable');
  document.querySelector('#firmware-screen-state').textContent = statusLabel(firmware.status || 'not-loaded');
  const logs = state.logs || [];
  document.querySelector('#log-list').innerHTML = logs.length
    ? logs.slice(-8).reverse().map((entry) => `<div class="log-entry">${escapeHtml(entry)}</div>`).join('')
    : `<div class="empty-state">${escapeHtml(t('logs.waiting'))}</div>`;
}

function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = value;
  return node.innerHTML;
}

function firmwareBytes(value) {
  if (!Number.isFinite(value)) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(2)} MB`;
}

function firmwareStatusLabel(status) {
  const map = {
    validated: 'fwStatus.validated',
    uploaded: 'fwStatus.uploaded',
    invalid: 'fwStatus.invalid',
    running: 'fwStatus.running',
    unavailable: 'fwStatus.unavailable',
    exited: 'fwStatus.exited',
    error: 'fwStatus.error',
    'not-running': 'fwStatus.notRunning',
  };
  const key = map[status];
  return key ? t(key) : String(status || t('status.unknown')).replaceAll('-', ' ').toUpperCase();
}

function playSourceLabel(play) {
  return t(play.is_official ? 'playSource.official' : 'playSource.community');
}

function playTitle(play) {
  return play.title?.zh || play.title?.en || play.slug;
}

function playDescription(play) {
  return play.description?.zh || play.description?.en || t('detail.noDescription');
}

function playCatalogIsOpen() {
  const modal = document.querySelector('#play-catalog-modal');
  return Boolean(modal && !modal.hidden && modal.classList.contains('is-open'));
}

function openPlayCatalog() {
  const modal = document.querySelector('#play-catalog-modal');
  if (!modal) return;
  if (playCatalogCloseTimer) window.clearTimeout(playCatalogCloseTimer);
  playCatalogPreviousFocus = document.activeElement;
  modal.hidden = false;
  document.body.classList.add('modal-open');
  window.requestAnimationFrame(() => modal.classList.add('is-open'));
  window.requestAnimationFrame(() => document.querySelector('#play-search')?.focus({preventScroll: true}));
}

function closePlayCatalog() {
  const modal = document.querySelector('#play-catalog-modal');
  if (!modal || modal.hidden) return;
  modal.classList.remove('is-open');
  document.body.classList.remove('modal-open');
  playCatalogCloseTimer = window.setTimeout(() => {
    if (!modal.classList.contains('is-open')) modal.hidden = true;
  }, 190);
  if (playCatalogPreviousFocus && document.contains(playCatalogPreviousFocus)) playCatalogPreviousFocus.focus({preventScroll: true});
  playCatalogPreviousFocus = null;
}

function trapPlayCatalogFocus(event) {
  if (!playCatalogIsOpen() || event.key !== 'Tab') return;
  const modal = document.querySelector('#play-catalog-modal');
  const focusable = [...modal.querySelectorAll('button, input, select, a[href]')]
    .filter((element) => !element.disabled && !element.hidden && element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function setFirmwareNotice(message, tone = '') {
  const notice = document.querySelector('#firmware-notice');
  notice.textContent = message;
  notice.className = `firmware-notice ${tone}`.trim();
}

function renderFirmwareArtifact(artifact, run = null) {
  lastFirmwarePanel = { artifact, run };
  const fields = {
    chip: document.querySelector('#firmware-chip'),
    format: document.querySelector('#firmware-format'),
    entry: document.querySelector('#firmware-entry'),
    size: document.querySelector('#firmware-size'),
    sha256: document.querySelector('#firmware-sha256'),
    partitions: document.querySelector('#firmware-partitions'),
    state: document.querySelector('#firmware-run-state'),
    reason: document.querySelector('#firmware-run-reason'),
    log: document.querySelector('#firmware-log'),
    source: document.querySelector('#firmware-source'),
  };
  if (!artifact) {
    Object.values(fields).forEach((field) => { field.textContent = '—'; });
    fields.state.textContent = t('fwStatus.notRunning');
    fields.reason.textContent = t('panel.loadFirst');
    fields.log.textContent = t('fw.logEmpty');
    fields.source.textContent = t('source.localUpload');
    document.querySelector('#firmware-analyze').disabled = true;
    document.querySelector('#firmware-run').disabled = true;
    document.querySelector('#firmware-stop').disabled = true;
    renderScreen({ status: 'not-loaded', message: 'NO FIRMWARE LOADED' });
    return;
  }
  const analysis = artifact.analysis || {};
  const factory = analysis.factory_image || {};
  const execution = artifact.execution || {};
  const runInfo = run || execution;
  selectedFirmwareId = artifact.id;
  fields.chip.textContent = t('seg.count', {chip: analysis.chip || t('status.unknown'), count: analysis.segment_count || 0});
  fields.format.textContent = analysis.format || '—';
  fields.entry.textContent = factory.entry_addr || analysis.entry_addr || '—';
  fields.size.textContent = firmwareBytes(artifact.size_bytes);
  fields.sha256.textContent = artifact.sha256 || '—';
  fields.partitions.textContent = (analysis.partitions || [])
    .map((part) => `${part.name}@0x${Number(part.offset).toString(16)}`).join(' · ') || t('partitions.noTable');
  fields.state.textContent = firmwareStatusLabel(execution.status);
  fields.reason.textContent = translateReason(execution.reason)
    || (execution.qemu_available ? t('panel.qemuDetected') : t('panel.qemuMissing'));
  fields.log.textContent = (runInfo.output || []).join('\n') || t('panel.noOutput');
  fields.source.textContent = artifact.source?.play_title
    ? `${artifact.source.play_title} · ${artifact.source.play_source === 'official' ? t('source.official') : t('source.community')}`
    : t('source.localUpload');
  document.querySelector('#firmware-analyze').disabled = false;
  document.querySelector('#firmware-run').disabled = execution.status === 'running';
  document.querySelector('#firmware-stop').disabled = execution.status !== 'running';
  setFirmwareNotice(`${artifact.filename} · ${firmwareStatusLabel(artifact.load_status)}`, artifact.load_status === 'validated' ? 'success' : 'warning');
  const screenMessage = execution.status === 'running' ? 'FIRMWARE RUNNING / DISPLAY OUTPUT UNAVAILABLE'
    : execution.status === 'unavailable' ? 'FIRMWARE LOADED / EXECUTION UNAVAILABLE'
    : execution.status === 'error' ? 'FIRMWARE EXECUTION ERROR'
    : execution.status === 'exited' ? 'FIRMWARE EXECUTION EXITED' : 'FIRMWARE READY';
  const screenState = {
    status: execution.status === 'running' ? 'running' : execution.status === 'unavailable' ? 'unavailable' : execution.status === 'error' ? 'error' : execution.status === 'exited' ? 'exited' : 'ready',
    message: screenMessage,
    filename: artifact.filename,
    execution_status: execution.status,
    reason: execution.reason,
    ...(runInfo.framebuffer || {}),
  };
  if (screenState.frame_rgb565_b64 || !hasNativeFrame || execution.status !== 'running') {
    renderScreen(screenState);
  }
}

function updateFirmwareSelect(items) {
  const select = document.querySelector('#firmware-artifact-select');
  const current = selectedFirmwareId;
  select.innerHTML = items.length
    ? items.map((item) => `<option value="${item.id}">${escapeHtml(item.filename)} · ${firmwareBytes(item.size_bytes)}</option>`).join('')
    : `<option value="">${escapeHtml(t('fw.select.empty'))}</option>`;
  if (current && items.some((item) => item.id === current)) select.value = String(current);
}

async function syncFirmwareToSession(artifactId) {
  if (!sessionId || !artifactId) return;
  const response = await fetch(`/api/sessions/${sessionId}/firmware`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({artifact_id: artifactId}),
  });
  if (!response.ok) return;
  const data = await response.json();
  render(data.state);
}

async function loadFirmwareList(preferredId = null) {
  const response = await fetch('/api/firmware');
  if (!response.ok) throw new Error(t('list.unavailable'));
  const data = await response.json();
  updateFirmwareSelect(data.firmware || []);
  if (data.firmware?.length) await selectFirmware(preferredId || data.firmware[0].id);
  else renderFirmwareArtifact(null);
}

function setPlayNotice(message, tone = '') {
  const notice = document.querySelector('#play-notice');
  notice.textContent = message;
  notice.className = `play-notice ${tone}`.trim();
}

function renderPlayCategories() {
  const select = document.querySelector('#play-category');
  const current = select.value;
  const categories = [...new Map(playCatalog.map((play) => {
    const key = play.category?.zh || play.category?.en || '未分类';
    return [key, play.category?.zh || play.category?.en || key];
  })).entries()].sort((left, right) => left[1].localeCompare(right[1], 'zh-CN'));
  select.innerHTML = `<option value="">${escapeHtml(t('catalog.allCategories'))}</option>`
    + categories.map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join('');
  if (categories.some(([key]) => key === current)) select.value = current;
}

function filteredPlays() {
  const query = document.querySelector('#play-search').value.trim().toLowerCase();
  const category = document.querySelector('#play-category').value;
  return playCatalog.filter((play) => {
    const haystack = [playTitle(play), play.title?.en, playDescription(play), play.author, play.category?.zh, play.category?.en]
      .filter(Boolean).join(' ').toLowerCase();
    return (!query || haystack.includes(query)) && (!category || (play.category?.zh || play.category?.en) === category);
  });
}

function renderPlayList() {
  const list = document.querySelector('#play-list');
  const items = filteredPlays();
  document.querySelector('#play-catalog-count').textContent = t('catalog.count', {shown: items.length, total: playCatalog.length});
  if (!items.length) {
    list.innerHTML = `<div class="catalog-empty">${escapeHtml(t('catalog.noMatch'))}</div>`;
    return;
  }
  list.innerHTML = items.map((play) => {
    const selected = play.slug === selectedPlaySlug ? ' is-selected' : '';
    const firmware = play.firmware || {};
    return `<button class="play-row${selected}" type="button" data-play-slug="${escapeHtml(play.slug)}">
      <span class="play-row-index">${String(playCatalog.indexOf(play) + 1).padStart(3, '0')}</span>
      <span class="play-row-main"><strong>${escapeHtml(playTitle(play))}</strong><small>${escapeHtml(play.author || t('catalog.unknownAuthor'))} · ${escapeHtml(play.category?.zh || play.category?.en || t('catalog.uncategorized'))}</small></span>
      <span class="play-row-meta"><b class="source-badge ${play.is_official ? 'official' : 'community'}">${playSourceLabel(play)}</b><small>${firmwareBytes(firmware.size)}</small></span>
    </button>`;
  }).join('');
  list.querySelectorAll('[data-play-slug]').forEach((element) => {
    element.addEventListener('click', () => selectPlay(element.dataset.playSlug));
  });
}

function renderPlayDetail(play) {
  const title = document.querySelector('#play-detail-title');
  const description = document.querySelector('#play-detail-description');
  const tags = document.querySelector('#play-detail-tags');
  const download = document.querySelector('#play-download');
  const run = document.querySelector('#play-run');
  const github = document.querySelector('#play-github');
  if (!play) {
    title.textContent = t('detail.select');
    description.textContent = t('detail.default');
    tags.innerHTML = '';
    ['#play-detail-author', '#play-detail-size', '#play-detail-format', '#play-detail-status', '#play-detail-sha'].forEach((id) => { document.querySelector(id).textContent = '—'; });
    download.disabled = true;
    run.disabled = true;
    github.hidden = true;
    return;
  }
  const firmware = play.firmware || {};
  title.textContent = playTitle(play);
  description.textContent = playDescription(play);
  tags.innerHTML = `<span class="source-badge ${play.is_official ? 'official' : 'community'}">${playSourceLabel(play)}</span><span class="detail-tag">${escapeHtml(play.category?.zh || play.category?.en || t('catalog.uncategorized'))}</span><span class="detail-tag">${escapeHtml(play.status || t('status.unknown'))}</span>`;
  document.querySelector('#play-detail-author').textContent = play.author || '—';
  document.querySelector('#play-detail-size').textContent = firmwareBytes(firmware.size);
  document.querySelector('#play-detail-format').textContent = firmware.format || '—';
  document.querySelector('#play-detail-status').textContent = firmware.available ? t('detail.available') : t('detail.unavailable');
  document.querySelector('#play-detail-sha').textContent = firmware.sha256 || '—';
  download.disabled = !firmware.available;
  run.disabled = !firmware.available;
  if (play.github_url) { github.href = play.github_url; github.hidden = false; } else github.hidden = true;
}

function selectPlay(slug) {
  selectedPlaySlug = slug;
  renderPlayList();
  renderPlayDetail(playCatalog.find((play) => play.slug === slug) || null);
  setPlayNotice(t('play.notice'));
}

async function loadPlayCatalog(force = false) {
  const response = await fetch(`/api/plays${force ? '?refresh=1' : ''}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || t('play.loadFailed'));
  playCatalog = data.plays || [];
  document.querySelector('#play-catalog-eyebrow').textContent = t('catalog.eyebrow', {count: data.count ?? playCatalog.length});
  renderPlayCategories();
  renderPlayList();
  if (!selectedPlaySlug && playCatalog.length) selectPlay(playCatalog[0].slug);
}

async function importSelectedPlay(runAfterImport = false) {
  if (!selectedPlaySlug) return;
  const play = playCatalog.find((item) => item.slug === selectedPlaySlug);
  if (!play) return;
  document.querySelector('#play-download').disabled = true;
  document.querySelector('#play-run').disabled = true;
  setPlayNotice(t('play.downloading'));
  try {
    const response = await fetch(`/api/plays/${encodeURIComponent(selectedPlaySlug)}/download`, {method: 'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || t('play.downloadFailed'));
    const artifact = data.firmware || data.artifact;
    await loadFirmwareList(artifact.id);
    await selectFirmware(artifact.id);
    setPlayNotice(t('play.loadedOk', {title: playTitle(play)}) + (data.reused ? t('play.reusedNote') : ''), 'success');
    if (runAfterImport) await runFirmware();
  } catch (error) {
    setPlayNotice(error.message, 'error');
  } finally {
    document.querySelector('#play-download').disabled = !play.firmware?.available;
    document.querySelector('#play-run').disabled = !play.firmware?.available;
  }
}

async function selectFirmware(id) {
  if (!id) {
    selectedFirmwareId = null;
    renderFirmwareArtifact(null);
    return;
  }
  const response = await fetch(`/api/firmware/${id}`);
  if (!response.ok) return;
  const artifact = await response.json();
  document.querySelector('#firmware-artifact-select').value = String(artifact.id);
  renderFirmwareArtifact(artifact);
  await syncFirmwareToSession(artifact.id);
}

async function uploadFirmware() {
  const input = document.querySelector('#firmware-file');
  const file = input.files?.[0];
  if (!file) {
    setFirmwareNotice(t('upload.chooseFirst'), 'warning');
    return;
  }
  const body = new FormData();
  body.append('file', file);
  setFirmwareNotice(t('upload.reading'));
  const response = await fetch('/api/firmware', {method: 'POST', body});
  const data = await response.json();
  if (!response.ok) {
    setFirmwareNotice(data.error || t('upload.validationFailed'), 'error');
    return;
  }
  selectedFirmwareId = data.id;
  await loadFirmwareList();
  await selectFirmware(data.id);
}

async function analyzeFirmware() {
  if (!selectedFirmwareId) return;
  const response = await fetch(`/api/firmware/${selectedFirmwareId}/analyze`, {method: 'POST'});
  const data = await response.json();
  if (!response.ok) {
    setFirmwareNotice(data.error || t('upload.analysisFailed'), 'error');
    return;
  }
  renderFirmwareArtifact(data);
  await syncFirmwareToSession(data.id);
}

function stopFirmwarePolling() {
  if (firmwarePollTimer) {
    clearInterval(firmwarePollTimer);
    firmwarePollTimer = null;
  }
}

async function pollFirmwareRun() {
  if (!selectedFirmwareId) return;
  const response = await fetch(`/api/firmware/${selectedFirmwareId}/run?session_id=${encodeURIComponent(sessionId)}&include_frame=0`);
  if (!response.ok) return;
  const data = await response.json();
  renderFirmwareArtifact(data.firmware, data.run);
  if (data.run.status !== 'running') stopFirmwarePolling();
}

async function runFirmware() {
  if (!selectedFirmwareId) return;
  const response = await fetch(`/api/firmware/${selectedFirmwareId}/run`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId}),
  });
  const data = await response.json();
  if (!response.ok) {
    setFirmwareNotice(data.error || t('run.startFailed'), 'error');
    return;
  }
  if (data.session?.state) render(data.session.state);
  renderFirmwareArtifact(data.firmware, data.run);
  stopFirmwarePolling();
  if (data.run.status === 'running') firmwarePollTimer = setInterval(pollFirmwareRun, 2000);
}

async function stopFirmware() {
  if (!selectedFirmwareId) return;
  const response = await fetch(`/api/firmware/${selectedFirmwareId}/stop`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId}),
  });
  if (!response.ok) return;
  const data = await response.json();
  if (data.session?.state) render(data.session.state);
  renderFirmwareArtifact(data.firmware, data.run);
  stopFirmwarePolling();
}

async function command(payload) {
  if (!sessionId) return;
  const response = await fetch(`/api/sessions/${sessionId}/commands`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!response.ok) return;
  const data = await response.json();
  render(data.state);
}

function setButtonVisual(button, pressed) {
  document.querySelectorAll(`[data-button="${button}"]`).forEach((element) => {
    element.classList.toggle('is-pressed', pressed);
    if (!pressed) element.classList.remove('is-long');
  });
}

let audioStatusHoldUntil = 0;

function setAudioStatus(message, holdMs = 0) {
  const node = document.querySelector('#audio-status');
  if (node) node.textContent = message;
  // Keep explicit user-facing messages visible; the telemetry loop must not
  // immediately overwrite them.
  if (holdMs > 0) audioStatusHoldUntil = Date.now() + holdMs;
}

function ensureSpeakerContext() {
  if (!speaker.ctx) speaker.ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (speaker.ctx.state === 'suspended') speaker.ctx.resume();
  return speaker.ctx;
}

function base64ToInt16(base64) {
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  return new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
}

function int16ToBase64(pcm) {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let binary = '';
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

function playSpeakerFrame(frame) {
  if (!speaker.enabled || !frame || frame.dir === 'record' || !frame.samples) return;
  const rate = Number(frame.rate || 16000);
  const channels = Math.min(2, Math.max(1, Number(frame.channels || 1)));
  let pcm;
  try {
    pcm = base64ToInt16(frame.samples);
  } catch (error) {
    return;
  }
  if (!pcm.length || !Number.isFinite(rate) || rate < 8000) return;
  const ctx = ensureSpeakerContext();
  if (ctx.state !== 'running') {
    // Autoplay policy: the context cannot play until the first user
    // gesture.  Schedule nothing -- a timeline built against a frozen clock
    // would go stale and mute the speaker after resume.
    return;
  }
  const framesPerChannel = Math.floor(pcm.length / channels);
  const audioBuffer = ctx.createBuffer(channels, framesPerChannel, rate);
  for (let channel = 0; channel < channels; channel += 1) {
    const target = audioBuffer.getChannelData(channel);
    for (let index = 0; index < framesPerChannel; index += 1) {
      target[index] = pcm[index * channels + channel] / 32768;
    }
  }
  const source = ctx.createBufferSource();
  source.buffer = audioBuffer;
  const sourceGain = ctx.createGain();
  source.connect(sourceGain);
  sourceGain.connect(ctx.destination);
  const now = ctx.currentTime;
  let resynced = false;
  // Keep one continuous timeline: frames are queued back to back on it, so
  // consecutive PCM chunks join sample-exactly without clicks.  Only two
  // corrections exist -- initial start, and recovering when the timeline
  // fell behind real time (context suspended, tab throttled, device stall).
  if (!speaker.nextTime || speaker.nextTime < now + SPEAKER_LEAD_FLOOR) {
    // Fade out whatever is still scheduled, then fade this buffer in: the
    // seam becomes a soft dip instead of a step-discontinuity click.  Only
    // touch automation times in the future -- past-time events are a browser
    // compatibility minefield (some engines throw, wedging the pump silent).
    if (speaker.lastSourceGain && speaker.lastSourceEnd > now) {
      const fadeStart = Math.max(now + 0.001, speaker.lastSourceEnd - SPEAKER_SEAM_FADE);
      const fadeEnd = Math.max(fadeStart + 0.001, speaker.lastSourceEnd);
      speaker.lastSourceGain.gain.setValueAtTime(1, fadeStart);
      speaker.lastSourceGain.gain.linearRampToValueAtTime(0.0001, fadeEnd);
    }
    speaker.nextTime = now + (speaker.nextTime ? SPEAKER_LEAD_RESET : 0.06);
    resynced = true;
  }
  // If the queue somehow ran far ahead (device stuck while muted, or a
  // pathological burst), skip stale backlog instead of stretching latency.
  if (speaker.nextTime > now + 1.2) return;
  const lead = speaker.nextTime - now;
  const rateAdj = Math.max(SPEAKER_ADJ_MIN, Math.min(SPEAKER_ADJ_MAX, 1 - (SPEAKER_LEAD_TARGET - lead) * SPEAKER_LEAD_GAIN));
  source.playbackRate.value = rateAdj;
  if (resynced) {
    sourceGain.gain.setValueAtTime(0.0001, speaker.nextTime);
    sourceGain.gain.linearRampToValueAtTime(1, speaker.nextTime + SPEAKER_SEAM_FADE);
  }
  source.start(speaker.nextTime);
  speaker.nextTime += audioBuffer.duration / rateAdj;
  speaker.lastSourceGain = sourceGain;
  speaker.lastSourceEnd = speaker.nextTime;
}

function scheduleSpeakerFrames(frames) {
  for (const frame of frames || []) {
    if (!frame || frame.revision === undefined || frame.revision <= speaker.consumed) continue;
    speaker.consumed = frame.revision;
    try {
      playSpeakerFrame(frame);
    } catch (error) {
      // A scheduling bug must never wedge the pump into permanent silence:
      // report it loudly and drop the whole timeline so the next frame
      // starts from a clean state.
      console.error('speaker scheduling failed:', error);
      setAudioStatus(t('audio.error', {message: String((error && error.message) || error)}), 10000);
      speaker.nextTime = 0;
      speaker.lastSourceGain = null;
      speaker.lastSourceEnd = 0;
    }
  }
}

async function speakerPumpTick() {
  const artifactId = selectedFirmwareId;
  if (!speaker.enabled || !artifactId) return;
  if (speaker.consumedArtifact !== artifactId) {
    speaker.consumedArtifact = artifactId;
    speaker.consumed = -1;
    speaker.nextTime = 0;
  }
  try {
    const response = await fetch(`/api/firmware/${artifactId}/audio?since=${speaker.consumed}`);
    if (!response.ok) return;
    const data = await response.json();
    if (data.latest < speaker.consumed) {
      // The firmware process restarted; its revision counter began anew.
      speaker.consumed = -1;
      speaker.nextTime = 0;
    }
    if (data.gap) {
      // History no longer reaches our position; resync to the newest frame.
      speaker.nextTime = 0;
      speaker.consumed = Math.max(0, Number(data.latest) - 1);
      scheduleSpeakerFrames((data.frames || []).slice(-1));
    } else {
      scheduleSpeakerFrames(data.frames);
    }
  } catch (error) {
    // Transient network hiccup -- the next tick retries; the timeline keeps
    // running so short fetch stalls do not click.
  }
}

function startSpeakerPump() {
  stopSpeakerPump();
  speaker.consumed = -1;
  speaker.consumedArtifact = selectedFirmwareId;
  speaker.nextTime = 0;
  speaker.lastSourceGain = null;
  speaker.lastSourceEnd = 0;
  speakerPumpTick();
  speaker.pumpTimer = window.setInterval(speakerPumpTick, 60);
}

function stopSpeakerPump() {
  if (speaker.pumpTimer) {
    window.clearInterval(speaker.pumpTimer);
    speaker.pumpTimer = 0;
  }
  speaker.nextTime = 0;
  speaker.lastSourceGain = null;
  speaker.lastSourceEnd = 0;
}

function floatToInt16(samples) {
  const out = new Int16Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    const value = Math.max(-1, Math.min(1, samples[index]));
    out[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return out;
}

async function startMicrophone() {
  if (mic.enabled || mic.requesting) return mic.enabled;
  if (!navigator.mediaDevices?.getUserMedia) {
    setAudioStatus(t('audio.unsupported'), 8000);
    return false;
  }
  mic.requesting = true;
  setAudioStatus(t('audio.requesting'), 30000);
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false},
    });
  } catch (error) {
    mic.requesting = false;
    setAudioStatus(t('audio.unavailable', {message: error.message || error.name || 'permission denied'}), 8000);
    return false;
  }
  mic.requesting = false;
  const ctx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 16000});
  const source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(2048, 1, 1);
  processor.onaudioprocess = (event) => {
    if (!mic.enabled || !sessionId) return;
    const pcm = floatToInt16(event.inputBuffer.getChannelData(0));
    if (!pcm.length) return;
    fetch(`/api/sessions/${sessionId}/audio`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({format: 'pcm-s16le', rate: ctx.sampleRate, channels: 1, samples: int16ToBase64(pcm)}),
    }).catch(() => {});
  };
  // ScriptProcessor only runs when connected to the graph; a muted gain node
  // avoids feeding the captured audio back out of the speakers.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  source.connect(processor);
  processor.connect(sink);
  sink.connect(ctx.destination);
  mic.enabled = true;
  mic.ctx = ctx;
  mic.stream = stream;
  mic.source = source;
  mic.processor = processor;
  mic.sink = sink;
  setAudioStatus(t('audio.live'), 30000);
  return true;
}

function stopMicrophone() {
  mic.enabled = false;
  try { mic.processor?.disconnect(); } catch (error) { /* already detached */ }
  try { mic.source?.disconnect(); } catch (error) { /* already detached */ }
  try { mic.sink?.disconnect(); } catch (error) { /* already detached */ }
  mic.stream?.getTracks().forEach((track) => track.stop());
  mic.ctx?.close().catch(() => {});
  mic.ctx = null;
  mic.stream = null;
  mic.source = null;
  mic.processor = null;
  mic.sink = null;
}

function updateAudioPanel(audio, firmwareState) {
  const bridgeNode = document.querySelector('#audio-bridge');
  const speakerButton = document.querySelector('#audio-speaker');
  const micButton = document.querySelector('#audio-mic');
  if (!bridgeNode || !speakerButton || !micButton) return;
  const bridge = audio?.bridge || 'unavailable';
  bridgeNode.textContent = bridge === 'json-lines' ? 'JSON-LINES' : t('bridge.unavailable');
  micButton.disabled = bridge !== 'json-lines' && !mic.enabled;
  if (mic.enabled || mic.requesting || Date.now() < audioStatusHoldUntil) return;
  const counters = {o: audio?.out_frames ?? 0, i: audio?.in_frames ?? 0};
  if (bridge !== 'json-lines') {
    setAudioStatus(t('audio.noBridge', counters));
  } else if (firmwareState && firmwareState.execution_status !== 'running') {
    // The queue is healthy but the firmware is not executing: nothing will
    // produce audio, so say that explicitly instead of a bare frame counter.
    setAudioStatus(t('audio.notRunning', counters));
  } else {
    setAudioStatus(t('audio.ready', counters));
  }
}

function buttonDown(button) {
  setButtonVisual(button, true);
  const state = { timer: 0, fired: false };
  holdState.set(button, state);
  // Holding any virtual key for LONG_PRESS_MS reports a firmware LONG event
  // once, exactly like keeping the physical side button pressed.  Releasing
  // before the threshold stays a short press (see buttonUp).
  state.timer = window.setTimeout(() => {
    state.fired = true;
    document.querySelectorAll(`[data-button="${button}"]`).forEach((element) => element.classList.add('is-long'));
    command({type: 'button', button, event: 'LONG'});
  }, LONG_PRESS_MS);
}

function buttonUp(button) {
  setButtonVisual(button, false);
  const state = holdState.get(button);
  if (!state) return;
  holdState.delete(button);
  if (state.timer) window.clearTimeout(state.timer);
  if (!state.fired) {
    // A virtual tap represents a short physical press.  The firmware input
    // bridge turns PRESS into a brief ADC-ladder pulse, which lets firmware
    // receive its normal PRESS and CLICK callbacks after the simulated
    // release.  Sending only CLICK here bypasses firmware that intentionally
    // reacts to low-latency PRESS input (for example Bad Apple).
    command({type: 'button', button, event: 'PRESS'});
  }
}

document.querySelectorAll('[data-button]').forEach((element) => {
  const button = element.dataset.button;
  element.addEventListener('pointerdown', (event) => { event.preventDefault(); buttonDown(button); });
  element.addEventListener('pointerup', (event) => { event.preventDefault(); buttonUp(button); });
  element.addEventListener('pointerleave', () => { if (element.classList.contains('is-pressed')) buttonUp(button); });
  element.addEventListener('pointercancel', () => buttonUp(button));
  // Touch long-press must not open the browser context menu mid-hold.
  element.addEventListener('contextmenu', (event) => event.preventDefault());
});
document.addEventListener('keydown', (event) => {
  if (playCatalogIsOpen()) {
    trapPlayCatalogFocus(event);
    if (event.key === 'Escape') {
      event.preventDefault();
      closePlayCatalog();
    }
    return;
  }
  if (event.repeat) return;
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) return;
  const button = event.key === 'ArrowUp' ? 'UP' : event.key === 'ArrowDown' ? 'DOWN' : event.key === 'Enter' ? 'OK' : null;
  if (button) { event.preventDefault(); buttonDown(button); }
});
document.addEventListener('keyup', (event) => {
  if (playCatalogIsOpen()) return;
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) return;
  const button = event.key === 'ArrowUp' ? 'UP' : event.key === 'ArrowDown' ? 'DOWN' : event.key === 'Enter' ? 'OK' : null;
  if (button) { event.preventDefault(); buttonUp(button); }
});
document.querySelector('#reset-button').addEventListener('click', () => command({type: 'reset'}));
function applySpeakerEnabled(enabled) {
  speaker.enabled = enabled;
  const button = document.querySelector('#audio-speaker');
  if (button) {
    button.classList.toggle('is-on', speaker.enabled);
    button.setAttribute('aria-pressed', String(speaker.enabled));
    button.querySelector('b').textContent = speaker.enabled ? t('state.on') : t('state.off');
  }
  try {
    localStorage.setItem('sim-speaker', speaker.enabled ? 'on' : 'off');
  } catch (error) {
    // localStorage may be unavailable (private mode) -- the toggle still works.
  }
  if (speaker.enabled) {
    ensureSpeakerContext();
    startSpeakerPump();
  } else {
    stopSpeakerPump();
  }
}

document.querySelector('#audio-speaker').addEventListener('click', () => {
  applySpeakerEnabled(!speaker.enabled);
  setAudioStatus(t(speaker.enabled ? 'audio.speakerOn' : 'audio.speakerOff'), 6000);
});

// The speaker choice survives page refreshes (the sim is refreshed often).
// Browsers still require a gesture before audio may play, so a restored
// pump waits for the first click anywhere and then starts audibly.
if (localStorage.getItem('sim-speaker') === 'on') {
  applySpeakerEnabled(true);
  const resumeOnGesture = () => {
    ensureSpeakerContext();
    document.removeEventListener('pointerdown', resumeOnGesture);
    document.removeEventListener('keydown', resumeOnGesture);
  };
  document.addEventListener('pointerdown', resumeOnGesture);
  document.addEventListener('keydown', resumeOnGesture);
}
document.querySelector('#audio-mic').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  if (mic.enabled) {
    stopMicrophone();
    button.classList.remove('is-on');
    button.setAttribute('aria-pressed', 'false');
    button.querySelector('b').textContent = t('state.off');
    setAudioStatus(t('audio.micStopped'), 6000);
    return;
  }
  const started = await startMicrophone();
  if (started) {
    button.classList.add('is-on');
    button.setAttribute('aria-pressed', 'true');
    button.querySelector('b').textContent = t('state.live');
  }
});
document.querySelector('#firmware-file').addEventListener('change', (event) => {
  lastFirmwareFileName = event.target.files?.[0]?.name || null;
  document.querySelector('#firmware-file-label').textContent = lastFirmwareFileName || t('fw.chooseBin');
});
document.querySelector('#firmware-upload').addEventListener('click', () => uploadFirmware().catch(() => setFirmwareNotice(t('upload.failed'), 'error')));
document.querySelector('#firmware-artifact-select').addEventListener('change', (event) => selectFirmware(Number(event.target.value)));
document.querySelector('#firmware-analyze').addEventListener('click', () => analyzeFirmware().catch(() => setFirmwareNotice(t('upload.analysisFailed'), 'error')));
document.querySelector('#firmware-run').addEventListener('click', () => runFirmware().catch(() => setFirmwareNotice(t('run.requestFailed'), 'error')));
document.querySelector('#firmware-stop').addEventListener('click', () => stopFirmware().catch(() => setFirmwareNotice(t('run.stopFailed'), 'error')));
document.querySelector('#lang-toggle').addEventListener('click', () => applyLanguage(uiLang === 'zh' ? 'en' : 'zh'));
document.querySelector('#play-search').addEventListener('input', renderPlayList);
document.querySelector('#play-category').addEventListener('change', renderPlayList);
document.querySelector('#play-refresh').addEventListener('click', () => loadPlayCatalog(true).catch((error) => setPlayNotice(error.message, 'error')));
document.querySelector('#play-download').addEventListener('click', () => importSelectedPlay(false));
document.querySelector('#play-run').addEventListener('click', () => importSelectedPlay(true));
document.querySelector('#open-play-catalog').addEventListener('click', openPlayCatalog);
document.querySelector('#close-play-catalog').addEventListener('click', closePlayCatalog);
document.querySelector('[data-play-catalog-close]').addEventListener('click', closePlayCatalog);

async function start() {
  applyLanguage(uiLang);
  const response = await fetch('/api/sessions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({}),
  });
  const data = await response.json();
  sessionId = data.session_id;
  runtimeOnline = true;
  sessionLabel.textContent = `${t('session.prefix')} ${sessionId.toUpperCase()}`;
  connectionLabel.textContent = t('conn.online');
  runtimeChip.textContent = t('chip.firmwareOnly');
  render(data.state);
  await loadFirmwareList();
  await loadPlayCatalog();
  const events = new EventSource(`/api/sessions/${sessionId}/stream`);
  events.onmessage = (event) => {
    const update = JSON.parse(event.data);
    render(update.state);
  };
  events.onerror = () => {
    connectionLabel.textContent = t('conn.reconnecting');
    runtimeChip.textContent = t('chip.linkLost');
  };
}

start().catch(() => {
  connectionLabel.textContent = t('conn.offline');
  runtimeChip.textContent = t('chip.error');
  renderScreen({status: 'error', message: 'RUNTIME OFFLINE', reason: 'The simulator worker could not be started.'});
});
