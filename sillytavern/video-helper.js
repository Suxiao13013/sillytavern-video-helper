// ==UserScript==
// @name         视频助手 - SillyTavern视频生成
// @version      v1.0.0
// @description  将SillyTavern聊天记录转化为动漫视频
// @author       Hermes AI Assistant
// @match        */*
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @connect      127.0.0.1
// @connect      localhost
// @connect      *
// ==/UserScript==

(function() {
    'use strict';

    // ============================================================
    //  配置与常量
    // ============================================================
    const SCRIPT_ID = 'video_helper_v1';
    const STORAGE_KEY = 'video_helper_settings';

    const DEFAULT_SETTINGS = {
        // 中间件API地址
        middlewareUrl: 'http://127.0.0.1:5000',
        
        // ComfyUI地址
        comfyuiUrl: 'http://127.0.0.1:8188',
        
        // 场景设置
        sceneDetection: 'auto',  // auto, fixed, user_input
        messagesPerScene: 5,
        maxScenes: 20,
        
        // 视频设置
        shotsPerScene: 4,
        secondsPerShot: 5,
        
        // 角色标签（手动覆盖）
        characterTags: {},
        
        // 启用状态
        enabled: true,
        autoProcess: false,  // 自动处理新消息
    };

    let settings = { ...DEFAULT_SETTINGS };

    // ============================================================
    //  工具函数
    // ============================================================
    
    function loadSettings() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved) {
                settings = { ...DEFAULT_SETTINGS, ...JSON.parse(saved) };
            }
        } catch (e) {
            console.error('[VideoHelper] 加载设置失败:', e);
        }
    }

    function saveSettings() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
        } catch (e) {
            console.error('[VideoHelper] 保存设置失败:', e);
        }
    }

    // 跨域请求
    function gmFetch(url, options = {}) {
        return new Promise((resolve, reject) => {
            if (typeof GM_xmlhttpRequest !== 'undefined') {
                GM_xmlhttpRequest({
                    method: options.method || 'GET',
                    url: url,
                    headers: options.headers || {},
                    data: options.body || undefined,
                    timeout: options.timeout || 120000,
                    onload: (response) => {
                        resolve({
                            ok: response.status >= 200 && response.status < 300,
                            status: response.status,
                            text: () => Promise.resolve(response.responseText),
                            json: () => Promise.resolve(JSON.parse(response.responseText))
                        });
                    },
                    onerror: (error) => reject(new Error(error.error || 'Network error')),
                    ontimeout: () => reject(new Error('Request timeout'))
                });
            } else {
                fetch(url, options).then(resolve).catch(reject);
            }
        });
    }

    // ============================================================
    //  聊天记录提取
    // ============================================================
    
    // 当前选中的消息索引集合
    let selectedMessageIndices = new Set();
    // 选择模式: 'all' | 'range' | 'selected'
    let selectionMode = 'all';
    
    /**
     * 从SillyTavern提取当前聊天记录
     * @param {Object} options - 选项
     * @param {string} options.mode - 'all' | 'range' | 'selected'
     * @param {number} options.start - range模式起始索引（含）
     * @param {number} options.end - range模式结束索引（含）
     * @param {Set} options.indices - selected模式的选中索引集合
     */
    function extractChatHistory(options = {}) {
        const chat = SillyTavern?.chat || window.chat || [];
        const messages = [];
        const mode = options.mode || selectionMode || 'all';
        
        for (let i = 0; i < chat.length; i++) {
            const msg = chat[i];
            if (!msg) continue;
            
            // 根据模式过滤
            if (mode === 'range') {
                const start = options.start ?? 0;
                const end = options.end ?? (chat.length - 1);
                if (i < start || i > end) continue;
            } else if (mode === 'selected') {
                const indices = options.indices || selectedMessageIndices;
                if (indices.size > 0 && !indices.has(i)) continue;
            }
            // mode === 'all' 不过滤
            
            messages.push({
                index: i,
                name: msg.name || '',
                is_user: msg.is_user || false,
                is_system: msg.is_system || false,
                send_date: msg.send_date || '',
                mes: msg.mes || '',
            });
        }
        
        return messages;
    }

    /**
     * 将聊天记录导出为JSONL格式
     */
    function exportChatAsJSONL(messages) {
        const lines = [];
        
        // 第一行：元数据
        const metadata = {
            chat_metadata: SillyTavern?.chatMetadata || {},
            user_name: SillyTavern?.name1 || 'User',
            character_name: SillyTavern?.name2 || 'Character'
        };
        lines.push(JSON.stringify(metadata));
        
        // 后续行：消息
        const msgs = messages || SillyTavern?.chat || window.chat || [];
        for (const msg of msgs) {
            if (!msg) continue;
            // 兼容原始chat对象和提取后的message对象
            const raw = msg.mes !== undefined ? msg : (SillyTavern?.chat?.[msg.index] || msg);
            lines.push(JSON.stringify({
                name: raw.name || msg.name || '',
                is_user: raw.is_user ?? msg.is_user ?? false,
                is_system: raw.is_system ?? msg.is_system ?? false,
                send_date: raw.send_date || msg.send_date || '',
                mes: raw.mes || msg.mes || '',
                extra: raw.extra || msg.extra || {}
            }));
        }
        
        return lines.join('\n');
    }

    /**
     * 获取聊天消息摘要（用于UI显示）
     */
    function getMessagePreview(index) {
        const chat = SillyTavern?.chat || window.chat || [];
        const msg = chat[index];
        if (!msg) return '';
        
        // 清理HTML标签获取纯文本
        let text = (msg.mes || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
        if (text.length > 80) text = text.substring(0, 80) + '...';
        return text;
    }

    /**
     * 获取聊天总消息数
     */
    function getChatLength() {
        const chat = SillyTavern?.chat || window.chat || [];
        return chat.length;
    }

    /**
     * 获取角色信息
     */
    function getCharacterInfo() {
        try {
            const charInfo = SillyTavern?.characters?.[SillyTavern?.this_chid] || {};
            return {
                name: charInfo.name || '',
                description: charInfo.description || '',
                personality: charInfo.personality || '',
                mes_example: charInfo.mes_example || '',
                avatar: charInfo.avatar || '',
                tags: charInfo.tags || []
            };
        } catch (e) {
            return { name: '', description: '' };
        }
    }

    // ============================================================
    //  中间件API通信
    // ============================================================
    
    /**
     * 测试中间件连接
     */
    async function testMiddlewareConnection() {
        try {
            const resp = await gmFetch(`${settings.middlewareUrl}/health`, {
                method: 'GET',
                timeout: 5000
            });
            if (resp.ok) {
                const data = await resp.json();
                return { success: true, data };
            }
            return { success: false, error: `HTTP ${resp.status}` };
        } catch (e) {
            return { success: false, error: e.message };
        }
    }

    /**
     * 发送聊天记录到中间件进行场景分析
     */
    async function analyzeScenes(messages) {
        const resp = await gmFetch(`${settings.middlewareUrl}/api/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: messages,
                settings: {
                    scene_detection: settings.sceneDetection,
                    messages_per_scene: settings.messagesPerScene,
                    max_scenes: settings.maxScenes,
                    character_tags: settings.characterTags
                }
            }),
            timeout: 60000
        });
        
        if (!resp.ok) {
            throw new Error(`分析失败: HTTP ${resp.status}`);
        }
        
        return await resp.json();
    }

    /**
     * 发送场景到中间件生成视频
     */
    async function generateVideo(scenes, characterInfo) {
        const resp = await gmFetch(`${settings.middlewareUrl}/api/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scenes: scenes,
                character_info: characterInfo,
                settings: {
                    shots_per_scene: settings.shotsPerScene,
                    seconds_per_shot: settings.secondsPerShot
                }
            }),
            timeout: 300000  // 5分钟超时
        });
        
        if (!resp.ok) {
            throw new Error(`生成失败: HTTP ${resp.status}`);
        }
        
        return await resp.json();
    }

    /**
     * 查询生成进度
     */
    async function getProgress(taskId) {
        const resp = await gmFetch(`${settings.middlewareUrl}/api/progress/${taskId}`, {
            timeout: 10000
        });
        
        if (!resp.ok) {
            throw new Error(`查询失败: HTTP ${resp.status}`);
        }
        
        return await resp.json();
    }

    // ============================================================
    //  UI界面
    // ============================================================
    
    function createUI() {
        // 移除旧UI
        const oldPanel = document.getElementById('video-helper-panel');
        if (oldPanel) oldPanel.remove();

        // 注入样式
        if (typeof GM_addStyle !== 'undefined') {
            GM_addStyle(getStyles());
        } else {
            const style = document.createElement('style');
            style.textContent = getStyles();
            document.head.appendChild(style);
        }

        // 创建主面板
        const panel = document.createElement('div');
        panel.id = 'video-helper-panel';
        panel.innerHTML = `
            <div class="vh-header">
                <span class="vh-title">🎬 视频助手</span>
                <div class="vh-header-buttons">
                    <button id="vh-btn-minimize" title="最小化">─</button>
                    <button id="vh-btn-close" title="关闭">✕</button>
                </div>
            </div>
            <div class="vh-body" id="vh-body">
                <!-- 标签页导航 -->
                <div class="vh-tabs">
                    <button class="vh-tab active" data-tab="scenes">📝 场景</button>
                    <button class="vh-tab" data-tab="generate">🎬 生成</button>
                    <button class="vh-tab" data-tab="settings">⚙️ 设置</button>
                </div>
                
                <!-- 场景标签页 -->
                <div class="vh-tab-content active" id="vh-tab-scenes">
                    <!-- 消息范围选择 -->
                    <div class="vh-section">
                        <div class="vh-section-title">📋 消息范围</div>
                        <div class="vh-range-mode">
                            <label class="vh-radio">
                                <input type="radio" name="vh-range-mode" value="all" checked>
                                <span>全部消息</span>
                            </label>
                            <label class="vh-radio">
                                <input type="radio" name="vh-range-mode" value="range">
                                <span>范围选择</span>
                            </label>
                            <label class="vh-radio">
                                <input type="radio" name="vh-range-mode" value="selected">
                                <span>勾选消息</span>
                            </label>
                        </div>
                        
                        <!-- 范围选择器 -->
                        <div class="vh-range-inputs" id="vh-range-inputs" style="display:none">
                            <div class="vh-range-row">
                                <label>从第</label>
                                <input type="number" id="vh-range-start" min="0" value="0" style="width:60px">
                                <label>到第</label>
                                <input type="number" id="vh-range-end" min="0" value="0" style="width:60px">
                                <label>条</label>
                                <button id="vh-btn-apply-range" class="vh-btn vh-btn-sm">应用</button>
                            </div>
                            <div class="vh-range-hint" id="vh-range-hint">共 0 条消息</div>
                        </div>
                        
                        <!-- 消息列表（勾选模式） -->
                        <div class="vh-msg-list" id="vh-msg-list" style="display:none">
                            <div class="vh-msg-list-toolbar">
                                <button id="vh-btn-select-all" class="vh-btn vh-btn-sm">全选</button>
                                <button id="vh-btn-select-none" class="vh-btn vh-btn-sm">清空</button>
                                <button id="vh-btn-select-ai" class="vh-btn vh-btn-sm">仅AI</button>
                                <button id="vh-btn-select-user" class="vh-btn vh-btn-sm">仅用户</button>
                                <button id="vh-btn-invert" class="vh-btn vh-btn-sm">反选</button>
                                <span class="vh-msg-selected-count" id="vh-selected-count">已选 0 条</span>
                            </div>
                            <div class="vh-msg-items" id="vh-msg-items">
                                <div class="vh-empty">加载中...</div>
                            </div>
                        </div>
                        
                        <!-- 当前选择摘要 -->
                        <div class="vh-selection-summary" id="vh-selection-summary">
                            当前: 全部消息
                        </div>
                    </div>
                    
                    <!-- 场景分析 -->
                    <div class="vh-section">
                        <div class="vh-section-title">🎬 场景分析</div>
                        <div class="vh-actions">
                            <button id="vh-btn-analyze" class="vh-btn vh-btn-primary">
                                🔍 分析场景
                            </button>
                            <select id="vh-scene-method">
                                <option value="auto">自动检测</option>
                                <option value="fixed">固定分段</option>
                                <option value="user_input">按用户输入</option>
                            </select>
                        </div>
                    </div>
                    <div id="vh-scenes-list" class="vh-scenes-list">
                        <div class="vh-empty">点击"分析场景"开始</div>
                    </div>
                    <div id="vh-scene-stats" class="vh-stats"></div>
                </div>
                
                <!-- 生成标签页 -->
                <div class="vh-tab-content" id="vh-tab-generate">
                    <div class="vh-actions">
                        <button id="vh-btn-generate" class="vh-btn vh-btn-success" disabled>
                            🎬 开始生成视频
                        </button>
                        <button id="vh-btn-export" class="vh-btn">
                            📥 导出JSONL
                        </button>
                    </div>
                    <div id="vh-progress" class="vh-progress" style="display:none">
                        <div class="vh-progress-bar">
                            <div class="vh-progress-fill" id="vh-progress-fill"></div>
                        </div>
                        <div class="vh-progress-text" id="vh-progress-text">准备中...</div>
                    </div>
                    <div id="vh-results" class="vh-results"></div>
                </div>
                
                <!-- 设置标签页 -->
                <div class="vh-tab-content" id="vh-tab-settings">
                    <div class="vh-settings">
                        <div class="vh-setting-group">
                            <label>中间件地址:</label>
                            <input type="text" id="vh-set-middleware" 
                                   value="${settings.middlewareUrl}" 
                                   placeholder="http://127.0.0.1:5000">
                        </div>
                        <div class="vh-setting-group">
                            <label>ComfyUI地址:</label>
                            <input type="text" id="vh-set-comfyui" 
                                   value="${settings.comfyuiUrl}"
                                   placeholder="http://127.0.0.1:8188">
                        </div>
                        <div class="vh-setting-group">
                            <label>场景检测方式:</label>
                            <select id="vh-set-detection">
                                <option value="auto" ${settings.sceneDetection==='auto'?'selected':''}>自动</option>
                                <option value="fixed" ${settings.sceneDetection==='fixed'?'selected':''}>固定分段</option>
                                <option value="user_input" ${settings.sceneDetection==='user_input'?'selected':''}>按用户输入</option>
                            </select>
                        </div>
                        <div class="vh-setting-group">
                            <label>每场景消息数:</label>
                            <input type="number" id="vh-set-msgcount" 
                                   value="${settings.messagesPerScene}" min="1" max="20">
                        </div>
                        <div class="vh-setting-group">
                            <label>最大场景数:</label>
                            <input type="number" id="vh-set-maxscenes" 
                                   value="${settings.maxScenes}" min="1" max="50">
                        </div>
                        <div class="vh-setting-group">
                            <label>每场景分镜数:</label>
                            <input type="number" id="vh-set-shots" 
                                   value="${settings.shotsPerScene}" min="1" max="10">
                        </div>
                        <div class="vh-setting-group">
                            <label>每分镜秒数:</label>
                            <input type="number" id="vh-set-seconds" 
                                   value="${settings.secondsPerShot}" min="1" max="15" step="0.5">
                        </div>
                        <div class="vh-actions">
                            <button id="vh-btn-test-conn" class="vh-btn">
                                🔗 测试连接
                            </button>
                            <button id="vh-btn-save-settings" class="vh-btn vh-btn-primary">
                                💾 保存设置
                            </button>
                        </div>
                        <div id="vh-conn-status" class="vh-conn-status"></div>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(panel);
        
        // 绑定事件
        bindEvents(panel);
        
        // 拖拽功能
        makeDraggable(panel);
        
        return panel;
    }

    function getStyles() {
        return `
            #video-helper-panel {
                position: fixed;
                top: 80px;
                right: 20px;
                width: 420px;
                max-height: 80vh;
                background: #1a1a2e;
                border: 1px solid #16213e;
                border-radius: 12px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.4);
                z-index: 10000;
                font-family: 'Segoe UI', sans-serif;
                color: #e0e0e0;
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            
            .vh-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px 16px;
                background: linear-gradient(135deg, #0f3460, #16213e);
                cursor: move;
                user-select: none;
            }
            
            .vh-title {
                font-size: 16px;
                font-weight: 600;
                color: #e94560;
            }
            
            .vh-header-buttons button {
                background: none;
                border: none;
                color: #888;
                cursor: pointer;
                font-size: 14px;
                padding: 2px 6px;
                margin-left: 4px;
            }
            
            .vh-header-buttons button:hover {
                color: #e94560;
            }
            
            .vh-body {
                padding: 12px;
                overflow-y: auto;
                max-height: calc(80vh - 50px);
            }
            
            .vh-tabs {
                display: flex;
                gap: 4px;
                margin-bottom: 12px;
            }
            
            .vh-tab {
                flex: 1;
                padding: 8px;
                background: #16213e;
                border: 1px solid #0f3460;
                border-radius: 6px;
                color: #888;
                cursor: pointer;
                font-size: 13px;
                text-align: center;
                transition: all 0.2s;
            }
            
            .vh-tab.active {
                background: #0f3460;
                color: #e94560;
                border-color: #e94560;
            }
            
            .vh-tab-content {
                display: none;
            }
            
            .vh-tab-content.active {
                display: block;
            }
            
            .vh-actions {
                display: flex;
                gap: 8px;
                margin-bottom: 12px;
                flex-wrap: wrap;
            }
            
            .vh-btn {
                padding: 8px 16px;
                border: 1px solid #333;
                border-radius: 6px;
                background: #16213e;
                color: #e0e0e0;
                cursor: pointer;
                font-size: 13px;
                transition: all 0.2s;
            }
            
            .vh-btn:hover {
                background: #1a1a3e;
            }
            
            .vh-btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            
            .vh-btn-primary {
                background: #0f3460;
                border-color: #e94560;
                color: #e94560;
            }
            
            .vh-btn-primary:hover {
                background: #e94560;
                color: white;
            }
            
            .vh-btn-success {
                background: #1b4332;
                border-color: #2d6a4f;
                color: #95d5b2;
            }
            
            .vh-btn-success:hover {
                background: #2d6a4f;
                color: white;
            }
            
            .vh-btn-sm {
                padding: 4px 10px;
                font-size: 12px;
                border-radius: 4px;
            }
            
            .vh-section {
                margin-bottom: 12px;
                padding: 10px;
                background: rgba(15, 52, 96, 0.3);
                border-radius: 8px;
                border: 1px solid #0f3460;
            }
            
            .vh-section-title {
                font-size: 13px;
                font-weight: 600;
                color: #e94560;
                margin-bottom: 8px;
            }
            
            .vh-range-mode {
                display: flex;
                gap: 12px;
                margin-bottom: 8px;
            }
            
            .vh-radio {
                display: flex;
                align-items: center;
                gap: 4px;
                font-size: 13px;
                color: #ccc;
                cursor: pointer;
            }
            
            .vh-radio input[type="radio"] {
                accent-color: #e94560;
            }
            
            .vh-range-inputs {
                margin: 8px 0;
                padding: 8px;
                background: #0f3460;
                border-radius: 6px;
            }
            
            .vh-range-row {
                display: flex;
                align-items: center;
                gap: 6px;
                font-size: 13px;
                color: #ccc;
            }
            
            .vh-range-row input {
                background: #16213e;
                border: 1px solid #333;
                border-radius: 4px;
                color: #e0e0e0;
                padding: 4px 6px;
                text-align: center;
            }
            
            .vh-range-hint {
                margin-top: 6px;
                font-size: 12px;
                color: #888;
            }
            
            .vh-msg-list {
                margin: 8px 0;
                border: 1px solid #0f3460;
                border-radius: 6px;
                overflow: hidden;
            }
            
            .vh-msg-list-toolbar {
                display: flex;
                gap: 4px;
                padding: 8px;
                background: #0f3460;
                flex-wrap: wrap;
                align-items: center;
            }
            
            .vh-msg-selected-count {
                margin-left: auto;
                font-size: 12px;
                color: #e94560;
                font-weight: 600;
            }
            
            .vh-msg-items {
                max-height: 250px;
                overflow-y: auto;
                background: #0d1b2a;
            }
            
            .vh-msg-item {
                display: flex;
                align-items: flex-start;
                gap: 8px;
                padding: 6px 10px;
                border-bottom: 1px solid #16213e;
                cursor: pointer;
                transition: background 0.15s;
                font-size: 12px;
            }
            
            .vh-msg-item:hover {
                background: #16213e;
            }
            
            .vh-msg-item.selected {
                background: rgba(233, 69, 96, 0.1);
                border-left: 3px solid #e94560;
            }
            
            .vh-msg-item input[type="checkbox"] {
                accent-color: #e94560;
                margin-top: 2px;
                flex-shrink: 0;
            }
            
            .vh-msg-idx {
                color: #666;
                min-width: 28px;
                text-align: right;
                flex-shrink: 0;
            }
            
            .vh-msg-role {
                font-weight: 600;
                min-width: 36px;
                flex-shrink: 0;
            }
            
            .vh-msg-role.ai {
                color: #66d9ef;
            }
            
            .vh-msg-role.user {
                color: #a6e22e;
            }
            
            .vh-msg-preview {
                color: #aaa;
                overflow: hidden;
                white-space: nowrap;
                text-overflow: ellipsis;
                flex: 1;
            }
            
            .vh-selection-summary {
                margin-top: 8px;
                padding: 6px 10px;
                font-size: 12px;
                color: #888;
                background: #16213e;
                border-radius: 4px;
                text-align: center;
            }
            
            .vh-scenes-list {
                max-height: 300px;
                overflow-y: auto;
                margin-bottom: 8px;
            }
            
            .vh-scene-item {
                padding: 10px;
                margin-bottom: 6px;
                background: #16213e;
                border-radius: 6px;
                border-left: 3px solid #e94560;
            }
            
            .vh-scene-title {
                font-weight: 600;
                color: #e94560;
                margin-bottom: 4px;
            }
            
            .vh-scene-meta {
                font-size: 12px;
                color: #888;
            }
            
            .vh-empty {
                text-align: center;
                color: #555;
                padding: 20px;
                font-style: italic;
            }
            
            .vh-stats {
                font-size: 12px;
                color: #888;
                padding: 8px;
                background: #0f3460;
                border-radius: 6px;
            }
            
            .vh-progress {
                margin: 12px 0;
            }
            
            .vh-progress-bar {
                height: 8px;
                background: #16213e;
                border-radius: 4px;
                overflow: hidden;
                margin-bottom: 8px;
            }
            
            .vh-progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #e94560, #0f3460);
                border-radius: 4px;
                transition: width 0.3s;
                width: 0%;
            }
            
            .vh-progress-text {
                font-size: 12px;
                color: #888;
                text-align: center;
            }
            
            .vh-results {
                margin-top: 12px;
            }
            
            .vh-result-item {
                padding: 10px;
                margin-bottom: 6px;
                background: #1b4332;
                border-radius: 6px;
                border-left: 3px solid #2d6a4f;
            }
            
            .vh-settings {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            
            .vh-setting-group {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            
            .vh-setting-group label {
                min-width: 100px;
                font-size: 13px;
                color: #aaa;
            }
            
            .vh-setting-group input,
            .vh-setting-group select {
                flex: 1;
                padding: 6px 10px;
                background: #0f3460;
                border: 1px solid #333;
                border-radius: 4px;
                color: #e0e0e0;
                font-size: 13px;
            }
            
            .vh-conn-status {
                margin-top: 8px;
                padding: 8px;
                border-radius: 6px;
                font-size: 13px;
                display: none;
            }
            
            .vh-conn-status.success {
                display: block;
                background: #1b4332;
                color: #95d5b2;
            }
            
            .vh-conn-status.error {
                display: block;
                background: #3d0000;
                color: #ff6b6b;
            }
        `;
    }

    function bindEvents(panel) {
        // 标签页切换
        panel.querySelectorAll('.vh-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                panel.querySelectorAll('.vh-tab').forEach(t => t.classList.remove('active'));
                panel.querySelectorAll('.vh-tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                panel.querySelector(`#vh-tab-${tab.dataset.tab}`).classList.add('active');
            });
        });
        
        // 最小化/关闭
        panel.querySelector('#vh-btn-minimize').addEventListener('click', () => {
            const body = panel.querySelector('#vh-body');
            body.style.display = body.style.display === 'none' ? 'block' : 'none';
        });
        
        panel.querySelector('#vh-btn-close').addEventListener('click', () => {
            panel.style.display = 'none';
        });
        
        // ---- 消息范围选择 ----
        const rangeInputs = panel.querySelector('#vh-range-inputs');
        const msgList = panel.querySelector('#vh-msg-list');
        const rangeHint = panel.querySelector('#vh-range-hint');
        const selectionSummary = panel.querySelector('#vh-selection-summary');
        
        // 初始化消息数提示
        const totalMsgs = getChatLength();
        rangeHint.textContent = `共 ${totalMsgs} 条消息 (索引 0 ~ ${totalMsgs - 1})`;
        panel.querySelector('#vh-range-end').value = totalMsgs - 1;
        
        // 选择模式切换
        panel.querySelectorAll('input[name="vh-range-mode"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                selectionMode = e.target.value;
                selectedMessageIndices.clear();
                
                rangeInputs.style.display = selectionMode === 'range' ? 'block' : 'none';
                msgList.style.display = selectionMode === 'selected' ? 'block' : 'none';
                
                if (selectionMode === 'all') {
                    selectionSummary.textContent = `当前: 全部消息 (${totalMsgs}条)`;
                } else if (selectionMode === 'range') {
                    updateRangeSummary(panel);
                } else if (selectionMode === 'selected') {
                    renderMessageList(panel);
                    updateSelectedCount(panel);
                }
            });
        });
        
        // 范围选择 - 应用按钮
        panel.querySelector('#vh-btn-apply-range').addEventListener('click', () => {
            updateRangeSummary(panel);
        });
        
        // 范围输入框变化时更新
        panel.querySelector('#vh-range-start').addEventListener('change', () => updateRangeSummary(panel));
        panel.querySelector('#vh-range-end').addEventListener('change', () => updateRangeSummary(panel));
        
        // 消息列表工具栏按钮
        panel.querySelector('#vh-btn-select-all').addEventListener('click', () => {
            const chat = SillyTavern?.chat || window.chat || [];
            for (let i = 0; i < chat.length; i++) {
                if (chat[i]) selectedMessageIndices.add(i);
            }
            refreshMessageListSelection(panel);
            updateSelectedCount(panel);
        });
        
        panel.querySelector('#vh-btn-select-none').addEventListener('click', () => {
            selectedMessageIndices.clear();
            refreshMessageListSelection(panel);
            updateSelectedCount(panel);
        });
        
        panel.querySelector('#vh-btn-select-ai').addEventListener('click', () => {
            const chat = SillyTavern?.chat || window.chat || [];
            selectedMessageIndices.clear();
            for (let i = 0; i < chat.length; i++) {
                if (chat[i] && !chat[i].is_user) selectedMessageIndices.add(i);
            }
            refreshMessageListSelection(panel);
            updateSelectedCount(panel);
        });
        
        panel.querySelector('#vh-btn-select-user').addEventListener('click', () => {
            const chat = SillyTavern?.chat || window.chat || [];
            selectedMessageIndices.clear();
            for (let i = 0; i < chat.length; i++) {
                if (chat[i] && chat[i].is_user) selectedMessageIndices.add(i);
            }
            refreshMessageListSelection(panel);
            updateSelectedCount(panel);
        });
        
        panel.querySelector('#vh-btn-invert').addEventListener('click', () => {
            const chat = SillyTavern?.chat || window.chat || [];
            const newSet = new Set();
            for (let i = 0; i < chat.length; i++) {
                if (chat[i] && !selectedMessageIndices.has(i)) newSet.add(i);
            }
            selectedMessageIndices = newSet;
            refreshMessageListSelection(panel);
            updateSelectedCount(panel);
        });
        
        // 分析场景
        panel.querySelector('#vh-btn-analyze').addEventListener('click', async () => {
            const btn = panel.querySelector('#vh-btn-analyze');
            btn.disabled = true;
            btn.textContent = '⏳ 分析中...';
            
            try {
                const messages = getSelectedMessages(panel);
                if (messages.length === 0) {
                    alert('当前没有选中的聊天记录');
                    return;
                }
                
                const method = panel.querySelector('#vh-scene-method').value;
                settings.sceneDetection = method;
                
                selectionSummary.textContent = `正在分析 ${messages.length} 条消息...`;
                
                const result = await analyzeScenes(messages);
                displayScenes(result.scenes);
                
                panel.querySelector('#vh-btn-generate').disabled = false;
                
                selectionSummary.textContent = `已分析 ${messages.length} 条消息，检测到 ${result.scenes?.length || 0} 个场景`;
                
            } catch (e) {
                alert(`分析失败: ${e.message}`);
            } finally {
                btn.disabled = false;
                btn.textContent = '🔍 分析场景';
            }
        });
        
        // 生成视频
        panel.querySelector('#vh-btn-generate').addEventListener('click', async () => {
            const btn = panel.querySelector('#vh-btn-generate');
            btn.disabled = true;
            
            try {
                const messages = getSelectedMessages(panel);
                const charInfo = getCharacterInfo();
                
                showProgress(true);
                updateProgress(0, '准备中...');
                
                const result = await generateVideo(messages, charInfo);
                
                if (result.task_id) {
                    pollProgress(result.task_id);
                } else if (result.success) {
                    displayResults(result);
                }
                
            } catch (e) {
                alert(`生成失败: ${e.message}`);
                btn.disabled = false;
            }
        });
        
        // 导出JSONL
        panel.querySelector('#vh-btn-export').addEventListener('click', () => {
            const messages = getSelectedMessages(panel);
            const jsonl = exportChatAsJSONL(messages);
            const blob = new Blob([jsonl], { type: 'application/jsonl' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `chat_${Date.now()}.jsonl`;
            a.click();
            URL.revokeObjectURL(url);
        });
        
        // 测试连接
        panel.querySelector('#vh-btn-test-conn').addEventListener('click', async () => {
            const status = panel.querySelector('#vh-conn-status');
            status.className = 'vh-conn-status';
            status.textContent = '测试中...';
            status.style.display = 'block';
            
            const result = await testMiddlewareConnection();
            
            if (result.success) {
                status.className = 'vh-conn-status success';
                status.textContent = '✅ 连接成功';
            } else {
                status.className = 'vh-conn-status error';
                status.textContent = `❌ 连接失败: ${result.error}`;
            }
        });
        
        // 保存设置
        panel.querySelector('#vh-btn-save-settings').addEventListener('click', () => {
            settings.middlewareUrl = panel.querySelector('#vh-set-middleware').value;
            settings.comfyuiUrl = panel.querySelector('#vh-set-comfyui').value;
            settings.sceneDetection = panel.querySelector('#vh-set-detection').value;
            settings.messagesPerScene = parseInt(panel.querySelector('#vh-set-msgcount').value);
            settings.maxScenes = parseInt(panel.querySelector('#vh-set-maxscenes').value);
            settings.shotsPerScene = parseInt(panel.querySelector('#vh-set-shots').value);
            settings.secondsPerShot = parseFloat(panel.querySelector('#vh-set-seconds').value);
            
            saveSettings();
            alert('设置已保存');
        });
    }
    
    // ---- 消息选择辅助函数 ----
    
    function getSelectedMessages(panel) {
        let options = {};
        if (selectionMode === 'range') {
            options = {
                mode: 'range',
                start: parseInt(panel.querySelector('#vh-range-start').value) || 0,
                end: parseInt(panel.querySelector('#vh-range-end').value) || 999
            };
        } else if (selectionMode === 'selected') {
            options = {
                mode: 'selected',
                indices: selectedMessageIndices
            };
        } else {
            options = { mode: 'all' };
        }
        return extractChatHistory(options);
    }
    
    function updateRangeSummary(panel) {
        const start = parseInt(panel.querySelector('#vh-range-start').value) || 0;
        const end = parseInt(panel.querySelector('#vh-range-end').value) || 0;
        const count = Math.max(0, end - start + 1);
        const summary = panel.querySelector('#vh-selection-summary');
        summary.textContent = `当前: 第${start}~${end}条 (共${count}条消息)`;
    }
    
    function renderMessageList(panel) {
        const container = panel.querySelector('#vh-msg-items');
        const chat = SillyTavern?.chat || window.chat || [];
        
        if (chat.length === 0) {
            container.innerHTML = '<div class="vh-empty">没有消息</div>';
            return;
        }
        
        // 虚拟化：只渲染前200条，滚动时加载更多
        const maxRender = Math.min(chat.length, 200);
        let html = '';
        
        for (let i = 0; i < maxRender; i++) {
            const msg = chat[i];
            if (!msg) continue;
            
            const isSelected = selectedMessageIndices.has(i);
            const role = msg.is_user ? 'user' : 'ai';
            const roleName = msg.is_user ? '用户' : 'AI';
            const preview = getMessagePreview(i);
            const name = (msg.name || '').substring(0, 12);
            
            html += `<div class="vh-msg-item ${isSelected ? 'selected' : ''}" data-idx="${i}">
                <input type="checkbox" ${isSelected ? 'checked' : ''} data-idx="${i}">
                <span class="vh-msg-idx">${i}</span>
                <span class="vh-msg-role ${role}">${roleName}</span>
                <span class="vh-msg-preview" title="${escapeHtml(preview)}">${escapeHtml(name)}: ${escapeHtml(preview)}</span>
            </div>`;
        }
        
        if (chat.length > maxRender) {
            html += `<div class="vh-empty">仅显示前${maxRender}条，请使用范围选择处理更多消息</div>`;
        }
        
        container.innerHTML = html;
        
        // 绑定点击事件
        container.querySelectorAll('.vh-msg-item').forEach(item => {
            item.addEventListener('click', (e) => {
                // 如果点击的是checkbox，不重复处理
                if (e.target.tagName === 'INPUT') return;
                
                const idx = parseInt(item.dataset.idx);
                toggleMessageSelection(idx, item, panel);
            });
        });
        
        container.querySelectorAll('.vh-msg-item input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const idx = parseInt(cb.dataset.idx);
                const item = cb.closest('.vh-msg-item');
                if (cb.checked) {
                    selectedMessageIndices.add(idx);
                    item.classList.add('selected');
                } else {
                    selectedMessageIndices.delete(idx);
                    item.classList.remove('selected');
                }
                updateSelectedCount(panel);
            });
        });
    }
    
    function toggleMessageSelection(idx, itemEl, panel) {
        if (selectedMessageIndices.has(idx)) {
            selectedMessageIndices.delete(idx);
            itemEl.classList.remove('selected');
            itemEl.querySelector('input[type="checkbox"]').checked = false;
        } else {
            selectedMessageIndices.add(idx);
            itemEl.classList.add('selected');
            itemEl.querySelector('input[type="checkbox"]').checked = true;
        }
        updateSelectedCount(panel);
    }
    
    function refreshMessageListSelection(panel) {
        const container = panel.querySelector('#vh-msg-items');
        container.querySelectorAll('.vh-msg-item').forEach(item => {
            const idx = parseInt(item.dataset.idx);
            const isSelected = selectedMessageIndices.has(idx);
            item.classList.toggle('selected', isSelected);
            const cb = item.querySelector('input[type="checkbox"]');
            if (cb) cb.checked = isSelected;
        });
    }
    
    function updateSelectedCount(panel) {
        const countEl = panel.querySelector('#vh-selected-count');
        const summary = panel.querySelector('#vh-selection-summary');
        const count = selectedMessageIndices.size;
        countEl.textContent = `已选 ${count} 条`;
        summary.textContent = count > 0 
            ? `当前: 已勾选 ${count} 条消息` 
            : '当前: 未选择任何消息 (将使用全部)';
    }

    function displayScenes(scenes) {
        const container = document.querySelector('#vh-scenes-list');
        const stats = document.querySelector('#vh-scene-stats');
        
        if (!scenes || scenes.length === 0) {
            container.innerHTML = '<div class="vh-empty">未检测到场景</div>';
            stats.textContent = '';
            return;
        }
        
        container.innerHTML = scenes.map(scene => `
            <div class="vh-scene-item">
                <div class="vh-scene-title">${escapeHtml(scene.title || `场景 ${scene.scene_id}`)}</div>
                <div class="vh-scene-meta">
                    ${scene.characters ? scene.characters.join(', ') : ''} 
                    ${scene.location ? '📍 ' + scene.location : ''}
                    · ${scene.messages || 0}条消息
                </div>
            </div>
        `).join('');
        
        stats.textContent = `共 ${scenes.length} 个场景，预计 ${scenes.length * settings.shotsPerScene * settings.secondsPerShot} 秒视频`;
    }

    function displayResults(result) {
        const container = document.querySelector('#vh-results');
        
        if (result.videos && result.videos.length > 0) {
            container.innerHTML = result.videos.map(v => `
                <div class="vh-result-item">
                    <div>✅ 场景${v.scene_id}: ${escapeHtml(v.title)}</div>
                    <div style="font-size:12px;color:#888">${v.path}</div>
                </div>
            `).join('');
            
            if (result.final_video) {
                container.innerHTML += `
                    <div class="vh-result-item" style="border-color:#e94560;background:#2a1a2e">
                        <div>🎬 最终视频: ${escapeHtml(result.final_video)}</div>
                    </div>
                `;
            }
        } else {
            container.innerHTML = '<div class="vh-empty">生成完成，但没有视频输出</div>';
        }
    }

    function showProgress(show) {
        document.querySelector('#vh-progress').style.display = show ? 'block' : 'none';
    }

    function updateProgress(percent, text) {
        document.querySelector('#vh-progress-fill').style.width = `${percent}%`;
        document.querySelector('#vh-progress-text').textContent = text;
    }

    async function pollProgress(taskId) {
        const panel = document.querySelector('#video-helper-panel');
        const generateBtn = panel.querySelector('#vh-btn-generate');
        
        const poll = async () => {
            try {
                const progress = await getProgress(taskId);
                
                updateProgress(
                    progress.percent || 0,
                    progress.message || `进度: ${progress.current || 0}/${progress.total || 0}`
                );
                
                if (progress.status === 'completed') {
                    displayResults(progress.result || {});
                    showProgress(false);
                    generateBtn.disabled = false;
                    return;
                }
                
                if (progress.status === 'error') {
                    alert(`生成出错: ${progress.error || '未知错误'}`);
                    showProgress(false);
                    generateBtn.disabled = false;
                    return;
                }
                
                // 继续轮询
                setTimeout(poll, 3000);
                
            } catch (e) {
                console.error('[VideoHelper] 轮询失败:', e);
                setTimeout(poll, 5000);
            }
        };
        
        poll();
    }

    function makeDraggable(element) {
        const header = element.querySelector('.vh-header');
        let isDragging = false;
        let startX, startY, startLeft, startTop;
        
        header.addEventListener('mousedown', (e) => {
            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
            const rect = element.getBoundingClientRect();
            startLeft = rect.left;
            startTop = rect.top;
            e.preventDefault();
        });
        
        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            element.style.left = `${startLeft + e.clientX - startX}px`;
            element.style.top = `${startTop + e.clientY - startY}px`;
            element.style.right = 'auto';
        });
        
        document.addEventListener('mouseup', () => {
            isDragging = false;
        });
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ============================================================
    //  初始化
    // ============================================================
    
    function init() {
        loadSettings();
        
        // 等待SillyTavern加载
        const waitForST = () => {
            if (typeof SillyTavern !== 'undefined' || document.querySelector('#chat')) {
                console.log('[VideoHelper] SillyTavern已就绪');
                createUI();
            } else {
                setTimeout(waitForST, 1000);
            }
        };
        
        // 延迟初始化
        if (document.readyState === 'complete') {
            setTimeout(waitForST, 2000);
        } else {
            window.addEventListener('load', () => setTimeout(waitForST, 2000));
        }
    }

    init();

})();
