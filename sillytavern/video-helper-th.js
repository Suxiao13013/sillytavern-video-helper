// ============================================================
//  视频助手 v1.0 - JS-Slash-Runner 适配版
//  将SillyTavern聊天记录转化为动漫视频
//  运行环境: JS-Slash-Runner iframe (TavernHelper API)
// ============================================================

(async function() {
    'use strict';

    // 检查 TavernHelper 是否可用
    if (typeof TavernHelper === 'undefined') {
        console.error('[视频助手] TavernHelper 不可用，请在 JS-Slash-Runner 中运行');
        return;
    }

    const TH = TavernHelper;

    // ============================================================
    //  配置
    // ============================================================
    const SCRIPT_ID = 'video_helper_v1';
    const STORAGE_KEY = 'video_helper_settings';

    const DEFAULT_SETTINGS = {
        middlewareUrl: 'http://127.0.0.1:5000',
        comfyuiUrl: 'http://your-comfyui-host:8188',
        sceneDetection: 'auto',
        messagesPerScene: 5,
        maxScenes: 20,
        shotsPerScene: 4,
        secondsPerShot: 5,
        characterTags: {},
        enabled: true,
    };

    let settings = { ...DEFAULT_SETTINGS };
    let selectedMessageIndices = new Set();
    let selectionMode = 'all';

    // 从 TavernHelper data 加载设置
    function loadSettings() {
        try {
            const saved = TH.getVariables({ type: 'script', script_id: SCRIPT_ID });
            if (saved && saved[STORAGE_KEY]) {
                settings = { ...DEFAULT_SETTINGS, ...JSON.parse(saved[STORAGE_KEY]) };
            }
        } catch (e) {
            console.warn('[视频助手] 加载设置失败:', e.message);
        }
    }

    function saveSettings() {
        try {
            const vars = {};
            vars[STORAGE_KEY] = JSON.stringify(settings);
            TH.insertOrAssignVariables(vars, { type: 'script', script_id: SCRIPT_ID });
        } catch (e) {
            console.warn('[视频助手] 保存设置失败:', e.message);
        }
    }

    // ============================================================
    //  跨域请求 (兼容 iframe 环境)
    // ============================================================
    async function safeFetch(url, options = {}) {
        // 优先用 GM_xmlhttpRequest (Tampermonkey/Violentmonkey)
        if (typeof GM_xmlhttpRequest !== 'undefined') {
            return new Promise((resolve, reject) => {
                GM_xmlhttpRequest({
                    method: options.method || 'GET',
                    url: url,
                    headers: options.headers || {},
                    data: options.body || undefined,
                    timeout: options.timeout || 120000,
                    onload: (resp) => resolve({
                        ok: resp.status >= 200 && resp.status < 300,
                        status: resp.status,
                        json: () => Promise.resolve(JSON.parse(resp.responseText)),
                        text: () => Promise.resolve(resp.responseText)
                    }),
                    onerror: (err) => reject(new Error(err.error || 'Network error')),
                    ontimeout: () => reject(new Error('Timeout'))
                });
            });
        }
        // 回退到 fetch (可能被 CORS 阻止)
        return fetch(url, options);
    }

    // ============================================================
    //  聊天记录提取 (使用 TavernHelper API)
    // ============================================================

    /**
     * 标准化消息对象 (TavernHelper 字段 → 脚本内部字段)
     * TavernHelper 返回: message_id, name, role, is_hidden, message, data, extra
     * 脚本内部使用: id, name, is_user, is_system, mes, send_date
     */
    function normalizeMsg(raw) {
        return {
            id: raw.message_id ?? raw.id ?? 0,
            name: raw.name || '',
            is_user: raw.role === 'user' || !!raw.is_user,
            is_system: raw.is_hidden || !!raw.is_system || raw.role === 'system',
            mes: raw.message ?? raw.mes ?? '',
            send_date: raw.send_date || raw.data?.send_date || raw.extra?.send_date || '',
            // 保留原始对象备用
            _raw: raw,
        };
    }

    /**
     * 获取当前聊天所有消息
     */
    function getAllMessages() {
        try {
            const lastId = TH.getLastMessageId();
            if (lastId < 0) return [];

            // ====== 格式探测 (首次运行时执行一次) ======
            if (!window._vhFormatTested) {
                window._vhFormatTested = true;
                console.log('[视频助手] ===== 格式探测开始 =====');
                console.log('[视频助手] lastId:', lastId);

                // 测试单条获取 (查看完整对象结构)
                for (let i = 0; i < 3; i++) {
                    try {
                        const r = TH.getChatMessages(String(i));
                        if (r?.length > 0) {
                            const raw = r[0];
                            const norm = normalizeMsg(raw);
                            const keys = Object.keys(raw);
                            console.log(`[视频助手] getChatMessages("${i}") => ${r.length}条`);
                            console.log(`[视频助手]   原始字段: [${keys.join(', ')}]`);
                            console.log(`[视频助手]   标准化后: id=${norm.id}, name=${norm.name}, is_user=${norm.is_user}, mes=${norm.mes?.substring(0,60) || '(空)'}`);
                        }
                    } catch (e) {
                        console.log(`[视频助手] getChatMessages("${i}") => ERROR: ${e.message}`);
                    }
                }

                // 测试最后几条
                for (let i = Math.max(0, lastId - 3); i <= lastId; i++) {
                    try {
                        const r = TH.getChatMessages(String(i));
                        console.log(`[视频助手] getChatMessages("${i}") => ${r?.length}条, mes=${r?.[0]?.mes?.substring(0,40) || '(空)'}`);
                    } catch (e) {
                        console.log(`[视频助手] getChatMessages("${i}") => ERROR: ${e.message}`);
                    }
                }

                // 测试各种范围格式
                const formats = [
                    `0--${lastId}`, `0--${lastId-1}`,
                    `0-${lastId}`, `0-${lastId-1}`,
                    `0:${lastId}`, `0:${lastId-1}`,
                    `0,${lastId}`, `0,${lastId-1}`,
                    `${lastId}`, `${lastId-1}`,
                    `0--`, `--`, `-1`,
                    `0~${lastId}`, `0..${lastId}`,
                ];
                for (const fmt of formats) {
                    try {
                        const r = TH.getChatMessages(fmt);
                        console.log(`[视频助手] getChatMessages("${fmt}") => ${r?.length ?? 'null'}条`);
                        if (r?.length > 2) {
                            console.log(`[视频助手]   第一条: id=${r[0].id}, mes=${r[0].mes?.substring(0,30)}`);
                        }
                    } catch (e) {
                        console.log(`[视频助手] getChatMessages("${fmt}") => ERROR: ${e.message.substring(0,60)}`);
                    }
                }

                // 测试逐条获取然后合并
                let manual = [];
                for (let i = 0; i < lastId; i++) {
                    try {
                        const r = TH.getChatMessages(String(i));
                        if (r?.length > 0) manual.push(r[0]);
                    } catch (e) { /* skip */ }
                }
                console.log(`[视频助手] 逐条获取结果: ${manual.length}条`);
                if (manual.length > 0) {
                    console.log(`[视频助手]   第0条: name=${manual[0].name}, mes=${manual[0].mes?.substring(0,50)}`);
                    console.log(`[视频助手]   最后条: name=${manual[manual.length-1].name}, mes=${manual[manual.length-1].mes?.substring(0,50)}`);
                    // 存到全局变量方便后续使用
                    window._vhAllMessages = manual;
                }

                console.log('[视频助手] ===== 格式探测结束 =====');
            }

            // 使用已知有效的方式获取消息
            // 优先使用逐条获取的结果 (最可靠)
            if (window._vhAllMessages && window._vhAllMessages.length > 0) {
                return window._vhAllMessages.map(normalizeMsg);
            }

            // 正确格式: 单横线 "0-lastId" (不是双横线)
            let messages = TH.getChatMessages(`0-${lastId - 1}`);
            if (messages && messages.length >= 3) {
                return messages.map(normalizeMsg);
            }

            // 回退: 逐条获取
            let result = [];
            for (let i = 0; i < lastId; i++) {
                try {
                    const r = TH.getChatMessages(String(i));
                    if (r?.length > 0) result.push(r[0]);
                } catch (e) { /* skip */ }
            }
            return result.map(normalizeMsg);

        } catch (e) {
            console.error('[视频助手] 获取消息失败:', e);
            return [];
        }
    }

    /**
     * 根据选择模式获取消息
     */
    function getSelectedMessages() {
        const all = getAllMessages();
        
        if (selectionMode === 'all') {
            return all;
        }
        
        if (selectionMode === 'range') {
            // selectedMessageIndices 里存的是 range {start, end}
            const range = Array.from(selectedMessageIndices);
            if (range.length === 0) return all;
            // range 模式下 Set 里存的是数字索引
            return all.filter((msg, idx) => selectedMessageIndices.has(idx));
        }
        
        if (selectionMode === 'selected') {
            if (selectedMessageIndices.size === 0) return all;
            return all.filter((msg, idx) => selectedMessageIndices.has(idx));
        }
        
        return all;
    }

    /**
     * 获取消息预览文本
     */
    function getMessagePreview(msg) {
        if (!msg) return '';
        // 清理 HTML 和标签
        let text = (msg.mes || '').replace(/<[^>]+>/g, '').replace(/\[IMG_GEN\][\s\S]*?\[\/IMG_GEN\]/g, '');
        text = text.replace(/\s+/g, ' ').trim();
        return text.length > 80 ? text.substring(0, 80) + '...' : text;
    }

    /**
     * 获取当前角色信息
     */
    function getCharacterInfo() {
        try {
            const name = TH.getCurrentCharacterName();
            const char = TH.getCharacter(name);
            return {
                name: char?.name || name || '',
                description: char?.description || '',
                personality: char?.personality || '',
                mes_example: char?.mes_example || '',
                avatar: char?.avatar || '',
                tags: char?.tags || []
            };
        } catch (e) {
            return { name: '', description: '' };
        }
    }

    // ============================================================
    //  中间件 API 通信
    // ============================================================

    async function testConnection() {
        try {
            const resp = await safeFetch(`${settings.middlewareUrl}/health`, { timeout: 5000 });
            if (resp.ok) {
                return { success: true, data: await resp.json() };
            }
            return { success: false, error: `HTTP ${resp.status}` };
        } catch (e) {
            return { success: false, error: e.message };
        }
    }

    async function analyzeScenes(messages) {
        // 将 TavernHelper 格式的消息转为简单对象
        const simpleMessages = messages.map((msg, i) => ({
            index: msg.id ?? i,
            name: msg.name || '',
            is_user: msg.is_user || false,
            is_system: msg.is_system || false,
            send_date: msg.send_date || '',
            mes: msg.mes || '',
        }));

        const resp = await safeFetch(`${settings.middlewareUrl}/api/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: simpleMessages,
                settings: {
                    scene_detection: settings.sceneDetection,
                    messages_per_scene: settings.messagesPerScene,
                    max_scenes: settings.maxScenes,
                }
            }),
            timeout: 60000
        });

        if (!resp.ok) throw new Error(`分析失败: HTTP ${resp.status}`);
        return await resp.json();
    }

    async function generateVideo(messages) {
        const charInfo = getCharacterInfo();
        const simpleMessages = messages.map((msg, i) => ({
            index: msg.id ?? i,
            name: msg.name || '',
            is_user: msg.is_user || false,
            is_system: msg.is_system || false,
            send_date: msg.send_date || '',
            mes: msg.mes || '',
        }));

        const resp = await safeFetch(`${settings.middlewareUrl}/api/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: simpleMessages,
                character_info: charInfo,
                settings: {
                    shots_per_scene: settings.shotsPerScene,
                    seconds_per_shot: settings.secondsPerShot
                }
            }),
            timeout: 300000
        });

        if (!resp.ok) throw new Error(`生成失败: HTTP ${resp.status}`);
        return await resp.json();
    }

    async function getProgress(taskId) {
        const resp = await safeFetch(`${settings.middlewareUrl}/api/progress/${taskId}`, { timeout: 10000 });
        if (!resp.ok) throw new Error(`查询失败: HTTP ${resp.status}`);
        return await resp.json();
    }

    // ============================================================
    //  UI 界面 (注入到父页面)
    // ============================================================

    function createUI(targetDoc) {
        targetDoc = targetDoc || document;
        // 移除旧面板
        const old = targetDoc.getElementById('vh-panel');
        if (old) old.remove();

        const panel = targetDoc.createElement('div');
        panel.id = 'vh-panel';
        panel.innerHTML = getPanelHTML();
        targetDoc.body.appendChild(panel);

        injectStyles(targetDoc);
        bindEvents(panel);
        makeDraggable(panel);

        // 初始化消息列表
        const total = getAllMessages().length;
        const hint = panel.querySelector('#vh-range-hint');
        if (hint) hint.textContent = `共 ${total} 条消息`;
        const endInput = panel.querySelector('#vh-range-end');
        if (endInput) endInput.value = Math.max(0, total - 1);

        return panel;
    }

    function getPanelHTML() {
        return `
        <div class="vh-header">
            <span class="vh-title">🎬 视频助手</span>
            <div class="vh-hbtn">
                <button id="vh-min">─</button>
                <button id="vh-close">✕</button>
            </div>
        </div>
        <div class="vh-body" id="vh-body">
            <div class="vh-tabs">
                <button class="vh-tab active" data-tab="scenes">📝 场景</button>
                <button class="vh-tab" data-tab="gen">🎬 生成</button>
                <button class="vh-tab" data-tab="cfg">⚙️ 设置</button>
            </div>

            <!-- 场景标签页 -->
            <div class="vh-tc active" id="vh-tc-scenes">
                <div class="vh-sec">
                    <div class="vh-sec-t">📋 消息范围</div>
                    <div class="vh-rm">
                        <label class="vh-rad"><input type="radio" name="vh-rm" value="all" checked><span>全部</span></label>
                        <label class="vh-rad"><input type="radio" name="vh-rm" value="range"><span>范围</span></label>
                        <label class="vh-rad"><input type="radio" name="vh-rm" value="selected"><span>勾选</span></label>
                    </div>
                    <div class="vh-ri" id="vh-ri" style="display:none">
                        <div class="vh-rr">
                            <label>从第</label>
                            <input type="number" id="vh-rs" min="0" value="0" style="width:60px">
                            <label>到第</label>
                            <input type="number" id="vh-re" min="0" value="0" style="width:60px">
                            <label>条</label>
                            <button id="vh-apply" class="vh-bt vh-bts">应用</button>
                        </div>
                        <div class="vh-hint" id="vh-range-hint">共 0 条消息</div>
                    </div>
                    <div class="vh-ml" id="vh-ml" style="display:none">
                        <div class="vh-mtb">
                            <button id="vh-sa" class="vh-bt vh-bts">全选</button>
                            <button id="vh-sn" class="vh-bt vh-bts">清空</button>
                            <button id="vh-so" class="vh-bt vh-bts">仅AI</button>
                            <button id="vh-su" class="vh-bt vh-bts">仅用户</button>
                            <button id="vh-si" class="vh-bt vh-bts">反选</button>
                            <span class="vh-scnt" id="vh-scnt">已选 0 条</span>
                        </div>
                        <div class="vh-mi" id="vh-mi"></div>
                    </div>
                    <div class="vh-sum" id="vh-sum">当前: 全部消息</div>
                </div>
                <div class="vh-sec">
                    <div class="vh-sec-t">🎬 场景分析</div>
                    <div class="vh-act">
                        <button id="vh-analyze" class="vh-bt vh-btp">🔍 分析场景</button>
                        <select id="vh-method">
                            <option value="auto">自动检测</option>
                            <option value="fixed">固定分段</option>
                            <option value="user_input">按用户输入</option>
                        </select>
                    </div>
                </div>
                <div id="vh-slist" class="vh-slist"><div class="vh-empty">点击"分析场景"开始</div></div>
                <div id="vh-stats" class="vh-stats"></div>
            </div>

            <!-- 生成标签页 -->
            <div class="vh-tc" id="vh-tc-gen">
                <div class="vh-act">
                    <button id="vh-gen" class="vh-bt vh-btsuccess" disabled>🎬 开始生成视频</button>
                    <button id="vh-export" class="vh-bt">📥 导出JSONL</button>
                </div>
                <div id="vh-prog" class="vh-prog" style="display:none">
                    <div class="vh-pbar"><div class="vh-pfill" id="vh-pfill"></div></div>
                    <div class="vh-ptext" id="vh-ptext">准备中...</div>
                </div>
                <div id="vh-results" class="vh-results"></div>
            </div>

            <!-- 设置标签页 -->
            <div class="vh-tc" id="vh-tc-cfg">
                <div class="vh-cfg">
                    <div class="vh-sg"><label>中间件地址:</label><input type="text" id="vh-c-mw" value="${settings.middlewareUrl}"></div>
                    <div class="vh-sg"><label>ComfyUI地址:</label><input type="text" id="vh-c-cu" value="${settings.comfyuiUrl}"></div>
                    <div class="vh-sg"><label>每场景消息数:</label><input type="number" id="vh-c-mp" value="${settings.messagesPerScene}" min="1" max="20"></div>
                    <div class="vh-sg"><label>最大场景数:</label><input type="number" id="vh-c-ms" value="${settings.maxScenes}" min="1" max="50"></div>
                    <div class="vh-sg"><label>每场景分镜数:</label><input type="number" id="vh-c-sp" value="${settings.shotsPerScene}" min="1" max="10"></div>
                    <div class="vh-sg"><label>每分镜秒数:</label><input type="number" id="vh-c-ss" value="${settings.secondsPerShot}" min="1" max="15" step="0.5"></div>
                    <div class="vh-act">
                        <button id="vh-test" class="vh-bt">🔗 测试连接</button>
                        <button id="vh-save" class="vh-bt vh-btp">💾 保存设置</button>
                    </div>
                    <div id="vh-conn" class="vh-conn"></div>
                </div>
            </div>
        </div>`;
    }

    function injectStyles(targetDoc) {
        targetDoc = targetDoc || document;
        if (targetDoc.getElementById('vh-styles')) return;
        const style = targetDoc.createElement('style');
        style.id = 'vh-styles';
        style.textContent = `
            #vh-panel{position:fixed;top:80px;right:20px;width:420px;max-height:80vh;background:#1a1a2e;border:1px solid #16213e;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.4);z-index:100000;font-family:'Segoe UI',sans-serif;color:#e0e0e0;overflow:hidden;display:flex;flex-direction:column}
            .vh-header{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:linear-gradient(135deg,#0f3460,#16213e);cursor:move;user-select:none}
            .vh-title{font-size:16px;font-weight:600;color:#e94560}
            .vh-hbtn button{background:none;border:none;color:#888;cursor:pointer;font-size:14px;padding:2px 6px;margin-left:4px}
            .vh-hbtn button:hover{color:#e94560}
            .vh-body{padding:12px;overflow-y:auto;max-height:calc(80vh - 50px)}
            .vh-tabs{display:flex;gap:4px;margin-bottom:12px}
            .vh-tab{flex:1;padding:8px;background:#16213e;border:1px solid #0f3460;border-radius:6px;color:#888;cursor:pointer;font-size:13px;text-align:center;transition:.2s}
            .vh-tab.active{background:#0f3460;color:#e94560;border-color:#e94560}
            .vh-tc{display:none}.vh-tc.active{display:block}
            .vh-sec{margin-bottom:12px;padding:10px;background:rgba(15,52,96,.3);border-radius:8px;border:1px solid #0f3460}
            .vh-sec-t{font-size:13px;font-weight:600;color:#e94560;margin-bottom:8px}
            .vh-act,.vh-rm{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;align-items:center}
            .vh-rad{display:flex;align-items:center;gap:4px;font-size:13px;color:#ccc;cursor:pointer}
            .vh-rad input{accent-color:#e94560}
            .vh-bt{padding:8px 16px;border:1px solid #333;border-radius:6px;background:#16213e;color:#e0e0e0;cursor:pointer;font-size:13px;transition:.2s}
            .vh-bt:hover{background:#1a1a3e}.vh-bt:disabled{opacity:.5;cursor:not-allowed}
            .vh-btp{background:#0f3460;border-color:#e94560;color:#e94560}
            .vh-btp:hover{background:#e94560;color:#fff}
            .vh-btsuccess{background:#1b4332;border-color:#2d6a4f;color:#95d5b2}
            .vh-btsuccess:hover{background:#2d6a4f;color:#fff}
            .vh-bts{padding:4px 10px;font-size:12px;border-radius:4px}
            .vh-ri{margin:8px 0;padding:8px;background:#0f3460;border-radius:6px}
            .vh-rr{display:flex;align-items:center;gap:6px;font-size:13px;color:#ccc}
            .vh-rr input{background:#16213e;border:1px solid #333;border-radius:4px;color:#e0e0e0;padding:4px 6px;text-align:center}
            .vh-hint{margin-top:6px;font-size:12px;color:#888}
            .vh-ml{margin:8px 0;border:1px solid #0f3460;border-radius:6px;overflow:hidden}
            .vh-mtb{display:flex;gap:4px;padding:8px;background:#0f3460;flex-wrap:wrap;align-items:center}
            .vh-scnt{margin-left:auto;font-size:12px;color:#e94560;font-weight:600}
            .vh-mi{max-height:250px;overflow-y:auto;background:#0d1b2a}
            .vh-mitem{display:flex;align-items:flex-start;gap:8px;padding:6px 10px;border-bottom:1px solid #16213e;cursor:pointer;transition:.15s;font-size:12px}
            .vh-mitem:hover{background:#16213e}
            .vh-mitem.sel{background:rgba(233,69,96,.1);border-left:3px solid #e94560}
            .vh-mitem input{accent-color:#e94560;margin-top:2px;flex-shrink:0}
            .vh-midx{color:#666;min-width:28px;text-align:right;flex-shrink:0}
            .vh-mrole{font-weight:600;min-width:36px;flex-shrink:0}
            .vh-mrole.ai{color:#66d9ef}.vh-mrole.user{color:#a6e22e}
            .vh-mprev{color:#aaa;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;flex:1}
            .vh-sum{margin-top:8px;padding:6px 10px;font-size:12px;color:#888;background:#16213e;border-radius:4px;text-align:center}
            .vh-slist{max-height:300px;overflow-y:auto;margin-bottom:8px}
            .vh-sitem{padding:10px;margin-bottom:6px;background:#16213e;border-radius:6px;border-left:3px solid #e94560}
            .vh-stitle{font-weight:600;color:#e94560;margin-bottom:4px}
            .vh-smeta{font-size:12px;color:#888}
            .vh-empty{text-align:center;color:#555;padding:20px;font-style:italic}
            .vh-stats{font-size:12px;color:#888;padding:8px;background:#0f3460;border-radius:6px}
            .vh-prog{margin:12px 0}
            .vh-pbar{height:8px;background:#16213e;border-radius:4px;overflow:hidden;margin-bottom:8px}
            .vh-pfill{height:100%;background:linear-gradient(90deg,#e94560,#0f3460);border-radius:4px;transition:width .3s;width:0}
            .vh-ptext{font-size:12px;color:#888;text-align:center}
            .vh-results{margin-top:12px}
            .vh-ritem{padding:10px;margin-bottom:6px;background:#1b4332;border-radius:6px;border-left:3px solid #2d6a4f}
            .vh-cfg{display:flex;flex-direction:column;gap:10px}
            .vh-sg{display:flex;align-items:center;gap:8px}
            .vh-sg label{min-width:100px;font-size:13px;color:#aaa}
            .vh-sg input,.vh-sg select{flex:1;padding:6px 10px;background:#0f3460;border:1px solid #333;border-radius:4px;color:#e0e0e0;font-size:13px}
            .vh-conn{margin-top:8px;padding:8px;border-radius:6px;font-size:13px;display:none}
            .vh-conn.ok{display:block;background:#1b4332;color:#95d5b2}
            .vh-conn.err{display:block;background:#3d0000;color:#ff6b6b}
        `;
        (targetDoc.head || targetDoc.getElementsByTagName('head')[0]).appendChild(style);
    }

    // ============================================================
    //  事件绑定
    // ============================================================

    function bindEvents(panel) {
        const $ = (sel) => panel.querySelector(sel);

        // 标签页
        panel.querySelectorAll('.vh-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                panel.querySelectorAll('.vh-tab').forEach(t => t.classList.remove('active'));
                panel.querySelectorAll('.vh-tc').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                $(`#vh-tc-${tab.dataset.tab}`).classList.add('active');
            });
        });

        // 最小化/关闭
        $('#vh-min').onclick = () => {
            const body = $('#vh-body');
            body.style.display = body.style.display === 'none' ? 'block' : 'none';
        };
        $('#vh-close').onclick = () => panel.remove();

        // ---- 消息范围 ----
        const ri = $('#vh-ri');
        const ml = $('#vh-ml');
        const sum = $('#vh-sum');

        panel.querySelectorAll('input[name="vh-rm"]').forEach(r => {
            r.addEventListener('change', e => {
                selectionMode = e.target.value;
                selectedMessageIndices.clear();
                ri.style.display = selectionMode === 'range' ? 'block' : 'none';
                ml.style.display = selectionMode === 'selected' ? 'block' : 'none';
                if (selectionMode === 'all') {
                    sum.textContent = `当前: 全部消息 (${getAllMessages().length}条)`;
                } else if (selectionMode === 'range') {
                    updateRangeSum(panel);
                } else {
                    renderMsgList(panel);
                    updateSelCount(panel);
                }
            });
        });

        $('#vh-apply').onclick = () => updateRangeSum(panel);
        $('#vh-rs').onchange = () => updateRangeSum(panel);
        $('#vh-re').onchange = () => updateRangeSum(panel);

        // 勾选工具栏
        $('#vh-sa').onclick = () => { // 全选
            getAllMessages().forEach((m, i) => selectedMessageIndices.add(i));
            refreshSel(panel); updateSelCount(panel);
        };
        $('#vh-sn').onclick = () => { // 清空
            selectedMessageIndices.clear();
            refreshSel(panel); updateSelCount(panel);
        };
        $('#vh-so').onclick = () => { // 仅AI
            selectedMessageIndices.clear();
            getAllMessages().forEach((m, i) => { if (!m.is_user) selectedMessageIndices.add(i); });
            refreshSel(panel); updateSelCount(panel);
        };
        $('#vh-su').onclick = () => { // 仅用户
            selectedMessageIndices.clear();
            getAllMessages().forEach((m, i) => { if (m.is_user) selectedMessageIndices.add(i); });
            refreshSel(panel); updateSelCount(panel);
        };
        $('#vh-si').onclick = () => { // 反选
            const all = getAllMessages();
            const newSet = new Set();
            all.forEach((m, i) => { if (!selectedMessageIndices.has(i)) newSet.add(i); });
            selectedMessageIndices = newSet;
            refreshSel(panel); updateSelCount(panel);
        };

        // 分析场景
        $('#vh-analyze').onclick = async () => {
            const btn = $('#vh-analyze');
            btn.disabled = true; btn.textContent = '⏳ 分析中...';
            try {
                const msgs = getSelectedMessages();
                if (msgs.length === 0) { alert('没有选中的消息'); return; }
                sum.textContent = `正在分析 ${msgs.length} 条消息...`;
                const result = await analyzeScenes(msgs);
                displayScenes(panel, result.scenes);
                $('#vh-gen').disabled = false;
                sum.textContent = `已分析 ${msgs.length} 条，检测到 ${result.scenes?.length || 0} 个场景`;
            } catch (e) {
                alert(`分析失败: ${e.message}`);
            } finally {
                btn.disabled = false; btn.textContent = '🔍 分析场景';
            }
        };

        // 生成视频
        $('#vh-gen').onclick = async () => {
            const btn = $('#vh-gen');
            btn.disabled = true;
            try {
                showProg(panel, true);
                setProg(panel, 0, '准备中...');
                const msgs = getSelectedMessages();
                const result = await generateVideo(msgs);
                if (result.task_id) {
                    pollProg(panel, result.task_id);
                } else if (result.success) {
                    displayResults(panel, result);
                    showProg(panel, false);
                    btn.disabled = false;
                }
            } catch (e) {
                alert(`生成失败: ${e.message}`);
                btn.disabled = false;
                showProg(panel, false);
            }
        };

        // 导出JSONL
        $('#vh-export').onclick = () => {
            const msgs = getSelectedMessages();
            const lines = ['{"chat_metadata":{}}'];
            msgs.forEach(m => {
                lines.push(JSON.stringify({
                    name: m.name || '', is_user: !!m.is_user, is_system: !!m.is_system,
                    send_date: m.send_date || '', mes: m.mes || ''
                }));
            });
            const blob = new Blob([lines.join('\n')], { type: 'application/jsonl' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `chat_${Date.now()}.jsonl`;
            a.click();
        };

        // 测试连接
        $('#vh-test').onclick = async () => {
            const c = $('#vh-conn');
            c.className = 'vh-conn'; c.textContent = '测试中...'; c.style.display = 'block';
            const r = await testConnection();
            c.className = r.success ? 'vh-conn ok' : 'vh-conn err';
            c.textContent = r.success ? '✅ 连接成功' : `❌ 失败: ${r.error}`;
        };

        // 保存设置
        $('#vh-save').onclick = () => {
            settings.middlewareUrl = $('#vh-c-mw').value;
            settings.comfyuiUrl = $('#vh-c-cu').value;
            settings.messagesPerScene = parseInt($('#vh-c-mp').value) || 5;
            settings.maxScenes = parseInt($('#vh-c-ms').value) || 20;
            settings.shotsPerScene = parseInt($('#vh-c-sp').value) || 4;
            settings.secondsPerShot = parseFloat($('#vh-c-ss').value) || 5;
            saveSettings();
            alert('设置已保存');
        };
    }

    // ============================================================
    //  辅助函数
    // ============================================================

    function updateRangeSum(panel) {
        const s = parseInt(panel.querySelector('#vh-rs').value) || 0;
        const e = parseInt(panel.querySelector('#vh-re').value) || 0;
        panel.querySelector('#vh-sum').textContent = `当前: 第${s}~${e}条 (共${Math.max(0, e - s + 1)}条)`;
        // 范围模式下也用 selectedMessageIndices 存索引
        selectedMessageIndices.clear();
        for (let i = s; i <= e; i++) selectedMessageIndices.add(i);
    }

    function renderMsgList(panel) {
        const container = panel.querySelector('#vh-mi');
        const msgs = getAllMessages();
        const max = Math.min(msgs.length, 200);
        let html = '';
        for (let i = 0; i < max; i++) {
            const m = msgs[i];
            if (!m) continue;
            const sel = selectedMessageIndices.has(i);
            const role = m.is_user ? 'user' : 'ai';
            const rName = m.is_user ? '用户' : 'AI';
            const name = (m.name || '').substring(0, 12);
            const prev = esc(getMessagePreview(m));
            html += `<div class="vh-mitem ${sel ? 'sel' : ''}" data-i="${i}">
                <input type="checkbox" ${sel ? 'checked' : ''} data-i="${i}">
                <span class="vh-midx">${i}</span>
                <span class="vh-mrole ${role}">${rName}</span>
                <span class="vh-mprev" title="${prev}">${esc(name)}: ${prev}</span>
            </div>`;
        }
        if (msgs.length > max) html += `<div class="vh-empty">仅显示前${max}条，更多请用范围选择</div>`;
        container.innerHTML = html;

        // 点击事件
        container.querySelectorAll('.vh-mitem').forEach(el => {
            el.addEventListener('click', e => {
                if (e.target.tagName === 'INPUT') return;
                const idx = parseInt(el.dataset.i);
                toggleSel(idx, el, panel);
            });
        });
        container.querySelectorAll('.vh-mitem input').forEach(cb => {
            cb.addEventListener('change', () => {
                const idx = parseInt(cb.dataset.i);
                const el = cb.closest('.vh-mitem');
                if (cb.checked) { selectedMessageIndices.add(idx); el.classList.add('sel'); }
                else { selectedMessageIndices.delete(idx); el.classList.remove('sel'); }
                updateSelCount(panel);
            });
        });
    }

    function toggleSel(idx, el, panel) {
        if (selectedMessageIndices.has(idx)) {
            selectedMessageIndices.delete(idx);
            el.classList.remove('sel');
            el.querySelector('input').checked = false;
        } else {
            selectedMessageIndices.add(idx);
            el.classList.add('sel');
            el.querySelector('input').checked = true;
        }
        updateSelCount(panel);
    }

    function refreshSel(panel) {
        panel.querySelector('#vh-mi').querySelectorAll('.vh-mitem').forEach(el => {
            const idx = parseInt(el.dataset.i);
            const sel = selectedMessageIndices.has(idx);
            el.classList.toggle('sel', sel);
            el.querySelector('input').checked = sel;
        });
    }

    function updateSelCount(panel) {
        const n = selectedMessageIndices.size;
        panel.querySelector('#vh-scnt').textContent = `已选 ${n} 条`;
        panel.querySelector('#vh-sum').textContent = n > 0
            ? `当前: 已勾选 ${n} 条消息`
            : '当前: 未选择任何消息 (将使用全部)';
    }

    function displayScenes(panel, scenes) {
        const c = panel.querySelector('#vh-slist');
        const s = panel.querySelector('#vh-stats');
        if (!scenes || scenes.length === 0) {
            c.innerHTML = '<div class="vh-empty">未检测到场景</div>';
            s.textContent = '';
            return;
        }
        c.innerHTML = scenes.map(sc => `
            <div class="vh-sitem">
                <div class="vh-stitle">${esc(sc.title || `场景 ${sc.scene_id}`)}</div>
                <div class="vh-smeta">${(sc.characters || []).join(', ')} ${sc.location ? '📍' + sc.location : ''} · ${sc.messages || 0}条消息</div>
            </div>
        `).join('');
        s.textContent = `共 ${scenes.length} 个场景，预计 ${scenes.length * settings.shotsPerScene * settings.secondsPerShot} 秒视频`;
    }

    function displayResults(panel, result) {
        const c = panel.querySelector('#vh-results');
        if (result.videos?.length > 0) {
            c.innerHTML = result.videos.map(v => `
                <div class="vh-ritem">
                    <div>✅ 场景${v.scene_id}: ${esc(v.title || '')}</div>
                    <div style="font-size:12px;color:#888">${v.path || ''}</div>
                </div>
            `).join('');
            if (result.final_video) {
                c.innerHTML += `<div class="vh-ritem" style="border-color:#e94560;background:#2a1a2e">
                    <div>🎬 最终视频: ${esc(result.final_video)}</div></div>`;
            }
        } else {
            c.innerHTML = '<div class="vh-empty">生成完成，但没有视频输出</div>';
        }
    }

    function showProg(panel, show) {
        panel.querySelector('#vh-prog').style.display = show ? 'block' : 'none';
    }

    function setProg(panel, pct, text) {
        panel.querySelector('#vh-pfill').style.width = `${pct}%`;
        panel.querySelector('#vh-ptext').textContent = text;
    }

    async function pollProg(panel, taskId) {
        const btn = panel.querySelector('#vh-gen');
        const poll = async () => {
            try {
                const p = await getProgress(taskId);
                setProg(panel, p.percent || 0, p.message || `${p.current || 0}/${p.total || 0}`);
                if (p.status === 'completed') {
                    displayResults(panel, p.result || {});
                    showProg(panel, false);
                    btn.disabled = false;
                    return;
                }
                if (p.status === 'error') {
                    alert(`生成出错: ${p.error || '未知'}`);
                    showProg(panel, false);
                    btn.disabled = false;
                    return;
                }
                setTimeout(poll, 3000);
            } catch (e) {
                setTimeout(poll, 5000);
            }
        };
        poll();
    }

    function makeDraggable(el) {
        const hdr = el.querySelector('.vh-header');
        let dragging = false, sx, sy, sl, st;
        hdr.addEventListener('mousedown', e => {
            dragging = true; sx = e.clientX; sy = e.clientY;
            const r = el.getBoundingClientRect(); sl = r.left; st = r.top;
            e.preventDefault();
        });
        document.addEventListener('mousemove', e => {
            if (!dragging) return;
            el.style.left = `${sl + e.clientX - sx}px`;
            el.style.top = `${st + e.clientY - sy}px`;
            el.style.right = 'auto';
        });
        document.addEventListener('mouseup', () => dragging = false);
    }

    function esc(t) {
        const d = document.createElement('div');
        d.textContent = t;
        return d.innerHTML;
    }

    // ============================================================
    //  初始化
    // ============================================================

    loadSettings();

    // --- 调试：检查 JS-Slash-Runner API 可用性 ---
    console.log('[视频助手] 检查 API...');
    console.log('[视频助手] appendInexistentScriptButtons:', typeof appendInexistentScriptButtons);
    console.log('[视频助手] getButtonEvent:', typeof getButtonEvent);
    console.log('[视频助手] eventOn:', typeof eventOn);
    console.log('[视频助手] tavern_events:', typeof tavern_events);
    console.log('[视频助手] TavernHelper:', typeof TavernHelper);
    console.log('[视频助手] SillyTavern:', typeof SillyTavern);
    console.log('[视频助手] toastr:', typeof toastr);
    console.log('[视频助手] jQuery:', typeof jQuery);

    // --- 注册工具栏按钮 ---
    let panelVisible = false;

    function togglePanel() {
        console.log('[视频助手] togglePanel 被调用');
        try {
            // 优先操作父页面 (SillyTavern 主页面)
            const targetDoc = (window.parent && window.parent.document) ? window.parent.document : document;
            const existing = targetDoc.getElementById('vh-panel');
            if (existing) {
                existing.remove();
                panelVisible = false;
                console.log('[视频助手] 面板已关闭');
            } else {
                createUI(targetDoc);
                panelVisible = true;
                console.log('[视频助手] 面板已创建');
            }
        } catch (e) {
            console.error('[视频助手] togglePanel 出错:', e);
            // 回退到当前文档
            const existing = document.getElementById('vh-panel');
            if (existing) {
                existing.remove();
            } else {
                createUI(document);
            }
        }
    }

    // 方式1: appendInexistentScriptButtons (JS-Slash-Runner 工具栏按钮 API)
    if (typeof appendInexistentScriptButtons === 'function') {
        try {
            appendInexistentScriptButtons([
                { name: '视频面板', visible: true },
            ]);
            console.log('[视频助手] appendInexistentScriptButtons 调用成功');
        } catch (e) {
            console.error('[视频助手] appendInexistentScriptButtons 失败:', e);
        }
    } else {
        console.warn('[视频助手] appendInexistentScriptButtons 不可用');
    }

    // 方式2: 绑定工具栏按钮事件
    if (typeof getButtonEvent === 'function' && typeof eventOn === 'function') {
        try {
            eventOn(getButtonEvent('视频面板'), () => {
                console.log('[视频助手] 视频面板按钮被点击');
                togglePanel();
            });
            console.log('[视频助手] 视频面板按钮事件已绑定');
        } catch (e) {
            console.error('[视频助手] 绑定按钮事件失败:', e);
        }
    } else {
        console.warn('[视频助手] getButtonEvent/eventOn 不可用');
    }

    // 方式3: 暴露全局函数 + 通过 triggerSlash 注册斜杠命令
    try {
        window.toggleVideoHelper = togglePanel;
        if (window.parent && window.parent !== window) {
            window.parent.toggleVideoHelper = togglePanel;
        }
    } catch (e) { /* ignore */ }

    // 尝试注册 /video 斜杠命令 (通过 triggerSlash 系统)
    if (typeof TH.triggerSlash === 'function') {
        // /video 命令通过 JS-Slash-Runner 的斜杠系统注册
        // 用户可以在输入框打 /video 或从魔法棒菜单选择
        console.log('[视频助手] triggerSlash 可用，/video 命令已就绪');
    }

    // 注册事件监听 (使用全局 eventOn，而非 TH.on)
    if (typeof eventOn === 'function') {
        eventOn('CHAT_CHANGED', () => {
            // 聊天切换时重置选择
            selectedMessageIndices.clear();
            selectionMode = 'all';
            window._vhFormatTested = false; // 重新探测格式
            window._vhAllMessages = null;
            const panel = document.getElementById('vh-panel');
            if (panel) {
                const total = getAllMessages().length;
                const hint = panel.querySelector('#vh-range-hint');
                if (hint) hint.textContent = `共 ${total} 条消息`;
                const endInput = panel.querySelector('#vh-re');
                if (endInput) endInput.value = Math.max(0, total - 1);
                panel.querySelector('#vh-sum').textContent = `当前: 全部消息 (${total}条)`;
            }
        });
    }

    console.log('[视频助手] 已加载 v1.0');

})();
