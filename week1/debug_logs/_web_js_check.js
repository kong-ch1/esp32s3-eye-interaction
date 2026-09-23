
const $ = (id) => document.getElementById(id);
const fmt = (v, n = 3) => (v === null || v === undefined) ? '—' : Number(v).toFixed(n);
const mag = (x, y, z) => Math.sqrt(x * x + y * y + z * z);
/* 字节 → MB，保留 2 位小数；null/undefined 显示为"—"而不是 0 */
const mb = (b) => (b === null || b === undefined) ? '—' : (Number(b) / 1048576).toFixed(2);
/* 取数值，缺失时为 null（不要退化成 0，避免假读数） */
const num = (v) => (v === null || v === undefined) ? null : Number(v);

const SERIES = [['ax', '加速度 X', '#378ADD'], ['ay', '加速度 Y', '#1D9E75'], ['az', '加速度 Z', '#BA7517']];
let LAST = [];
let PAUSED = false;      /* 板子当前是否处于「暂停周期上报」，由 /api/latest 带回来 */

/* 服务端 API 地址自适应：
 *   从服务器打开（http/https）→ 跟随当前地址（本机/局域网/内网穿透/云上都通用）；
 *   直接双击 html（file://）→ 退回本机 8000 端口。 */
const API_BASE = (location.protocol === 'http:' || location.protocol === 'https:')
  ? location.origin
  : 'http://127.0.0.1:8000';

/* ---------- 摄像头取流方式 ----------
 * 'server'（默认）：走服务器中转，网页不必知道板子 IP，板子只维持 1 路连接。
 * 'direct'：网页直连板子 81 端口，仅用于排查故障（板子最多扛 2 路）。 */
const CAM_MODE = 'server';
const CAM_BASE = 'http://10.1.41.160:81';   // 仅 'direct' 模式使用
const CAM_STREAM   = CAM_MODE === 'server' ? API_BASE + '/api/camera' : CAM_BASE + '/stream';
const CAM_HEARTBEAT = CAM_MODE === 'server' ? API_BASE + '/api/camera/status' : CAM_BASE + '/ping';

/* ---------- 摄像头：自带掉线自愈 ----------
 * 1) 每次重连换 URL 时间戳，避开浏览器缓存的失败结果；
 * 2) 失败后 2s→4s…封顶 10s 退避重试，不一次性判死；
 * 3) 每 3 秒心跳：状态端点超时 = 服务被堵死 → 重建；
 *    端点活着但报告无人观看 = 画面其实已停在最后一帧 → 同样重建。 */
(function mountCamera() {
  const img = document.getElementById('camStream');
  const st  = document.getElementById('camStatus');
  const btn = document.getElementById('btnCamRe');
  if (!img) return;

  let retries = 0, retryTimer = null, everOK = false, deadVotes = 0, zeroVotes = 0;
  const text = (s) => { st.textContent = s; };

  function connect() {
    clearTimeout(retryTimer);
    text(retries === 0 ? '连接中…' : '第 ' + retries + ' 次重连…');
    img.onload = function () { retries = 0; zeroVotes = 0; everOK = true; text('实时画面 · 已连接'); };
    img.onerror = function () {
      if (++retries > 20) { text('未连接 · 请给板子重新上电后刷新本页面'); return; }
      const wait = Math.min(2 * retries, 10);
      text('未连接 · ' + wait + ' 秒后重试（第 ' + retries + ' 次）');
      retryTimer = setTimeout(connect, wait * 1000);
    };
    img.src = CAM_STREAM + '?nocache=' + Date.now();
  }

  function hardReconnect(reason) {
    text('检测到' + reason + '，正在重建连接…');
    retries = 0; zeroVotes = 0; deadVotes = 0;
    img.onload = img.onerror = null;
    img.src = '';                       // 先掐断，释放服务端通路
    setTimeout(connect, 300);
  }

  function parseLive(r, body) {
    if (CAM_MODE === 'server') {
      try {
        const j = JSON.parse(body);
        const age = j.last_frame_age;
        const flowing = j.state === 'streaming' && age !== null && age < 3;
        const note = age === null ? '尚无画面' : ' ' + age.toFixed(1) + ' 秒前的帧';
        return { alive: flowing, note: note };
      } catch (e) { return { alive: false, note: '状态解析失败' }; }
    }
    const m = body.match(/clients=(\d+)\/(\d+)/);
    const clients = m ? Number(m[1]) : null;
    return { alive: r.ok && clients !== null && clients > 0, note: clients === null ? '' : clients + ' 路直连' };
  }

  async function heartbeat() {
    if (document.hidden) return;        // 页面在后台就别添乱
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 4000);
    try {
      const r = await fetch(CAM_HEARTBEAT + '?nocache=' + Date.now(),
                            { cache: 'no-store', signal: ctl.signal });
      const body = await r.text();
      clearTimeout(to);
      deadVotes = 0;
      const s = parseLive(r, body);
      if (!s.alive) {
        if (++zeroVotes >= 2) hardReconnect('画面已停止');
        else text('实时画面 · 已连接（' + s.note.trim() + '）');
      } else {
        zeroVotes = 0;
        text(everOK ? '实时画面 · 已连接（' + s.note.trim() + '）' : '服务在线，正在取画面…');
      }
    } catch (e) {
      clearTimeout(to);
      if (++deadVotes >= 2) {
        deadVotes = 0;
        hardReconnect(CAM_MODE === 'server' ? '服务器无响应' : '板子无响应');
      } else if (retries === 0) text('连接中…');
    }
  }

  btn.addEventListener('click', function () {
    retries = 0; deadVotes = 0; zeroVotes = 0; everOK = false;
    hardReconnect('手动重连');
  });

  connect();
  setInterval(heartbeat, 3000);
})();

function renderLegend() {
  $('legend').innerHTML = SERIES
    .map(([, name, c]) => `<span><i style="background:${c}"></i>${name}</span>`).join('');
}

/* 曲线绘制：只在"数据变了"或"尺寸变了"时才真正重画。
 * 原来每秒都重设 canvas.width/height（等于重新分配画布），是主要性能开销。 */
let lastSig = '';
function drawChart(force) {
  const cv = $('chart');
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 900;
  const h = 240;
  const needW = Math.round(w * dpr), needH = Math.round(h * dpr);

  const sig = LAST.length + ':' + (LAST[0] ? LAST[0].id : '') + ':' + (LAST[LAST.length - 1] ? LAST[LAST.length - 1].id : '');
  if (!force && sig === lastSig && cv.width === needW && cv.height === needH) return;
  lastSig = sig;

  if (cv.width !== needW || cv.height !== needH) { cv.width = needW; cv.height = needH; }
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);

  const pts = LAST.slice().reverse();      // 服务端返回倒序，这里还原成时间正序

  if (!pts.length) {
    g.fillStyle = '#6b6a65';
    g.font = '13px sans-serif';
    g.textAlign = 'center';
    g.fillText('暂无数据', w / 2, h / 2);
    return;
  }

  const pl = 48, pr = 12, pt = 10, pb = 24;
  let min = Infinity, max = -Infinity;
  pts.forEach(p => SERIES.forEach(([k]) => {
    const v = Number(p[k]);
    if (v < min) min = v;
    if (v > max) max = v;
  }));
  if (!isFinite(min) || !isFinite(max)) { min = -1; max = 1; }
  if (max - min < 1e-6) { min -= 0.5; max += 0.5; }
  const pad = (max - min) * 0.12;
  min -= pad; max += pad;

  const X = i => pl + (w - pl - pr) * (pts.length === 1 ? 0.5 : i / (pts.length - 1));
  const Y = v => pt + (h - pt - pb) * (1 - (v - min) / (max - min));

  g.font = '11px sans-serif';
  g.strokeStyle = '#ebeae5';
  g.lineWidth = 1;
  g.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = min + (max - min) * i / 4;
    const yy = Math.round(Y(v)) + 0.5;
    g.beginPath(); g.moveTo(pl, yy); g.lineTo(w - pr, yy); g.stroke();
    g.fillStyle = '#6b6a65';
    g.fillText(v.toFixed(2), pl - 8, yy + 4);
  }

  g.fillStyle = '#6b6a65';
  g.textAlign = 'left';
  g.fillText((pts[0].server_time || '').slice(11), pl, h - 6);
  g.textAlign = 'right';
  g.fillText((pts[pts.length - 1].server_time || '').slice(11), w - pr, h - 6);

  SERIES.forEach(([k, , color]) => {
    g.strokeStyle = color;
    g.lineWidth = 1.8;
    g.lineJoin = 'round';
    g.beginPath();
    pts.forEach((p, i) => {
      const xx = X(i), yy = Y(Number(p[k]));
      if (i === 0) g.moveTo(xx, yy); else g.lineTo(xx, yy);
    });
    g.stroke();
  });
}

window.addEventListener('resize', () => drawChart(true));

function setPill(cls, text) {
  const p = $('pill');
  p.className = 'pill ' + cls;
  p.textContent = text;
}

function clearAll() {
  setPill('idle', '暂无数据');
  $('dev').textContent = 'device: —';
  ['ax', 'ay', 'az', 'am', 'tp', 'hp', 'rf'].forEach(k => $(k).innerHTML = '—');
  $('hpUsed').textContent = '已用 — MB / 共 — MB';
  $('rfQ').textContent = '—';
  $('hpCard').title = '';
  $('mtime').textContent = $('mage').textContent = $('mseq').textContent = $('mip').textContent = '—';
  $('tbody').innerHTML = '<tr><td colspan="9" style="text-align:center;color:#6b6a65">暂无数据</td></tr>';
  LAST = [];
  drawChart(true);
}

/* 带超时的 JSON 拉取：服务器卡住时不至于让页面一直挂着请求 */
async function getJSON(url) {
  const ctl = new AbortController();
  const to = setTimeout(() => ctl.abort(), 6000);
  try {
    const r = await fetch(url, { cache: 'no-store', signal: ctl.signal });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } finally { clearTimeout(to); }
}

/* ================= 第3周：实体按键事件 =================
 *
 * 数据来自 /api/buttons，服务端按 request_id 把一次按键产生的几条聚合成一条。
 * 这里**刻意不画四阶段时间线** —— 实体按键没有下行通道，压根不存在
 * 「服务器受理」和「指令下发」两步，画出来就是假的证据。只列真实发生的：
 * 谁按的、什么时候、新采了几条、跨度多少。
 */
/* 暂停态的界面同步：状态徽标 + 按钮文案。
 * 按钮文案跟着状态走，避免出现"当前是暂停态、按钮却还写着暂停"这种自相矛盾。 */
function updatePauseUI() {
  const chip = $('modeChip'), btn = $('btnPause');
  if (chip) {
    chip.textContent = PAUSED ? '已暂停周期上报' : '上报中';
    chip.className = 'tag' + (PAUSED ? ' paused' : '');
    chip.title = PAUSED
      ? '板子已停掉 1Hz 传感上报，改用 /api/poll 心跳保持命令通道'
      : '板子每秒上报一次传感数据';
  }
  if (btn && !btn.disabled) {
    btn.textContent = PAUSED ? '恢复周期上报' : '暂停周期上报';
  }
}

function renderButtons(d) {
  const box = $('btnList');
  const evs = (d && d.events) || [];
  if (!evs.length) {
    $('btnCount').textContent = '等待按键…';
    box.innerHTML = '<div class="btnrow">还没有检测到实体按键事件 —— 去按一下板子上的键。</div>';
    return;
  }
  const newest = evs[0];
  $('btnCount').textContent = '最近 ' + evs.length + ' 次 · 最后一次 '
                            + newest.age_seconds.toFixed(0) + ' 秒前';

  box.innerHTML = evs.map(e => {
    const fresh = e.age_seconds < 20;      /* 20 秒内算"刚发生"，标出来便于对照操作 */
    /* 一次按键的 5 条数据本该在几秒内传完。跨度超过 30 秒说明这些记录压根
     * 不是同一次按键 —— 典型原因是事件 id 撞车（板子重启后计数器归零）。
     * 这个判断来自第3周真实踩过的坑，见 docs/design/09 第 3 节。 */
    const odd = e.span_ms > 30000;
    return `<div class="btnrow${fresh ? ' fresh' : ''}${odd ? ' odd' : ''}">` +
      `<span class="key">${e.button || '?'}</span>` +
      `<span>${e.first_time}</span>` +
      `<span>新采集 <b>${e.samples}</b> 条</span>` +
      `<span>记录 id <b>${e.first_id}</b> ~ <b>${e.last_id}</b></span>` +
      `<span>入库跨度 <b>${e.span_ms}</b> ms</span>` +
      (e.press_delay_ms === null || e.press_delay_ms === undefined ? '' :
        `<span>按下→采样 <b>${e.press_delay_ms}</b> ms</span>`) +
      `<span>${e.age_seconds.toFixed(0)} 秒前</span>` +
      (odd ? `<span class="warn" title="一次按键不会持续这么久，这些记录很可能不属于同一次按键">跨度异常 · 事件 id 可能撞车</span>` : '') +
      `<span style="font-family:ui-monospace,Consolas,monospace">${e.event_id}</span>` +
      `</div>`;
  }).join('');
}

async function tick() {
  if (document.hidden) return;            // 后台标签页不轮询
  try {
    /* 先取最新一条：它的 device_id 决定后面两个查询只看哪台设备。
     * 少了这一步，自测脚本（device_id=selftest-sim）模拟出来的按键事件
     * 会混进真板子的「实体交互」面板 —— 那等于让模拟数据冒充真机证据，
     * 正是本项目一直在防的那种"看起来像证据"的东西。 */
    const lr = await getJSON(API_BASE + '/api/latest');
    const dev = (lr.record && lr.record.device_id) || '';
    const q = dev ? '&device_id=' + encodeURIComponent(dev) : '';
    const [hr, br] = await Promise.all([
      getJSON(API_BASE + '/api/history?limit=60' + q),
      getJSON(API_BASE + '/api/buttons?limit=6' + q)
    ]);

    $('mthr').textContent = (lr.stale_seconds ?? '—') + ' 秒';

    if (!lr.has_data) { clearAll(); return; }

    const r = lr.record;
    $('dev').textContent = 'device: ' + r.device_id;
    $('ax').innerHTML = fmt(r.ax) + '<span class="u">g</span>';
    $('ay').innerHTML = fmt(r.ay) + '<span class="u">g</span>';
    $('az').innerHTML = fmt(r.az) + '<span class="u">g</span>';
    const m = mag(Number(r.ax), Number(r.ay), Number(r.az));
    $('am').innerHTML = fmt(m) + '<span class="u">g</span>';

    /* 设备自身状态。板子没上报时字段是 null —— 显示"—"而不是 0，
     * 免得把"没测到"伪造成"温度 0 度 / 内存 0 KB"这种假读数。 */
    const tp = (r.temp_c === null || r.temp_c === undefined) ? null : Number(r.temp_c);
    $('tp').innerHTML = tp === null ? '—' : fmt(tp, 1) + '<span class="u">°C</span>';

    /* 内存：剩余 = 板子上报值；已用 = 总量 - 剩余（同口径，都是 MALLOC_CAP_DEFAULT）。
     * 老固件没上报 total_heap 时，已用/总量都显示"—"，不硬算成 0。 */
    const heap     = num(r.free_heap);
    const heapMin  = num(r.min_free_heap);
    const heapTot  = num(r.total_heap);
    const heapUsed = (heap === null || heapTot === null) ? null : Math.max(0, heapTot - heap);
    /* 历史最低水位挂在 title 上：它持续下降 = 可能有内存泄漏 */
    $('hpCard').title = heapMin === null ? '' : '开机以来最低剩余：' + mb(heapMin) + ' MB';
    $('hp').innerHTML = heap === null ? '—' : mb(heap) + '<span class="u">MB</span>';
    $('hpUsed').textContent = '已用 ' + mb(heapUsed) + ' MB / 共 ' + mb(heapTot) + ' MB';

    /* WiFi 信号强度（dBm 是负数，越接近 0 越好） */
    const rssi = num(r.rssi);
    if (rssi === null) {
      $('rf').innerHTML = '—';
      $('rfQ').textContent = '—';
    } else {
      const q = rssi >= -60 ? '很好' : rssi >= -70 ? '尚可'
              : rssi >= -80 ? '偏弱，可能丢包' : '很差，必然断流';
      $('rf').innerHTML = rssi + '<span class="u">dBm</span>';
      $('rfQ').textContent = q;
    }

    $('mtime').textContent = r.server_time;
    $('mage').textContent = r.age_seconds.toFixed(1) + ' 秒';
    $('mseq').textContent = r.seq;
    $('mip').textContent = r.src_ip || '—';

    /* 三种状态必须分开，不能混成一句"未更新"：
     *   · 暂停中     —— 设备活得好好的，是**我们让它别上报**的；数据年龄变大是预期
     *   · 正在恢复   —— 刚发了 resume，心跳还在、数据还没来，给个过渡提示
     *   · 真未更新   —— 设备确实掉线了
     * 把第一种误判成第三种，就是冤假错案（而且会掩盖"暂停生效了"这个事实）。 */
    PAUSED = !!lr.paused;
    updatePauseUI();
    const seen = lr.device_seen_seconds;
    const seenTxt = (seen === null || seen === undefined)
      ? '—' : seen.toFixed(0) + ' 秒前';
    if (PAUSED) {
      setPill('warn', '已暂停周期上报 · 心跳 ' + seenTxt);
    } else if (r.stale && lr.last_request_kind === 'poll'
               && seen !== null && seen < 5) {
      setPill('warn', '正在恢复上报…（心跳仍在）');
    } else {
      setPill(r.stale ? 'bad' : 'ok',
              r.stale ? '未更新 · 已 ' + r.age_seconds.toFixed(0) + ' 秒' : '实时更新中');
    }

    LAST = hr.records || [];
    drawChart(false);
    renderButtons(br);

    const rows = LAST.slice(0, 8).map(x => {
      const mm = mag(Number(x.ax), Number(x.ay), Number(x.az));
      const t = (x.temp_c === null || x.temp_c === undefined) ? '—' : fmt(x.temp_c, 1);
      const xf = num(x.free_heap), xt = num(x.total_heap);
      const xu = (xf === null || xt === null) ? '—' : mb(Math.max(0, xt - xf));
      /* 第2/3周：把三种来源在表里区分开。依据是数据自带的 trigger 字段，
       * 不是界面上的猜测 —— 换台机器跑同样的数据也是这个结论。
       *   定时 = 板子自己按时上报（默认节奏）
       *   指令 = 网页点了「重新采集」，服务器下发的
       *   按键 = 现场有人按了板子上的实体键 */
      const isCmd = (x.trigger === 'command');
      const isBtn = (x.trigger === 'button');
      const tag = isCmd
        ? `<span class="tag cmd" title="${x.request_id || ''} · ${x.cmd_state || ''}">指令</span>`
        : isBtn
          ? `<span class="tag btn" title="${x.request_id || ''} · 按的是 ${x.button || ''} 键">按键 ${x.button || ''}</span>`
          : `<span class="tag">定时</span>`;
      return `<tr class="${isCmd ? 'is-cmd' : (isBtn ? 'is-btn' : '')}"><td>${x.server_time}</td>` +
             `<td>${tag}</td>` +
             `<td>${fmt(x.ax)}</td><td>${fmt(x.ay)}</td>` +
             `<td>${fmt(x.az)}</td><td>${fmt(mm)}</td><td>${t}</td>` +
             `<td>${mb(xf)}</td><td>${xu}</td>` +
             `<td>${x.seq}</td></tr>`;
    }).join('');
    $('tbody').innerHTML = rows || '<tr><td colspan="10" style="text-align:center;color:#6b6a65">暂无数据</td></tr>';
  } catch (e) {
    setPill('warn', '无法连接服务');
  }
}

/* ================= 第2周：主动交互（下行指令） =================
 *
 * 与「刷新页面」的本质区别：刷新只是 GET 已有的历史记录，数据条数不会变；
 * 这里会 POST 一条指令，服务器**受理**它，等板子真的执行完再回报。
 * 下面把四个必经阶段和各自耗时摊开显示，任何一步卡住都能看出来。
 */
const CMD_STEPS = [
  { key: 'issued',    name: '① 服务器受理', at: 'created_at',   leg: null },
  { key: 'delivered', name: '② 指令已下发', at: 'delivered_at', leg: 'issue_to_deliver_ms' },
  { key: 'received',  name: '③ 设备已接收', at: 'received_at',  leg: 'deliver_to_receive_ms' },
  /* 第4步刻意叫「执行结果」而不是「执行完成」：这一步有三种结局
   * （完成 / 设备回报失败 / 超时），名字里预设成"完成"的话，
   * 失败时会出现「执行完成 · 执行失败（设备回报）」这种自相矛盾的读法。 */
  { key: 'done',      name: '④ 执行结果',   at: 'done_at',      leg: 'receive_to_done_ms' },
];
const TERMINAL = ['done', 'timeout', 'failed'];

let CMD = { id: null, timer: null, totalBefore: null, last: null };

function renderTimeline(cmd) {
  if (!cmd) {
    $('cmdTimeline').innerHTML = CMD_STEPS.map(s =>
      `<div class="step"><div class="n">${s.name}</div><div class="t">等待</div></div>`
    ).join('');
    return;
  }
  const st = cmd.status;
  const t = cmd.timeout_stage;
  const legs = cmd.legs_ms || {};
  const terminal = TERMINAL.includes(st);
  /* 第一个还没到达的阶段 —— 它就是"当前停在哪一步"，无论是进行中还是超时。
   * 注意不能因为状态是终态就不标它，否则超时指令四步全灰，看不出卡在哪。 */
  const firstUnreached = CMD_STEPS.findIndex(s => !cmd[s.at]);

  $('cmdTimeline').innerHTML = CMD_STEPS.map((s, i) => {
    const reached = !!cmd[s.at];
    const isHere = !reached && i === firstUnreached;
    let cls, note;
    if (reached) {
      cls = 'done';
      note = (cmd[s.at] || '').slice(11);
    } else if (isHere) {
      cls = terminal ? 'bad' : 'cur';
      note = st === 'timeout'
        ? '超时（' + (t === 'accept' ? '设备未取走' : '未执行完') + '）'
        : (st === 'failed' ? '执行失败（设备回报）' : '进行中…');
    } else {
      cls = '';
      note = '等待';
    }
    const leg = (s.leg && legs[s.leg])
      ? `<div class="d">+${legs[s.leg]} ms</div>` : '<div class="d">&nbsp;</div>';
    return `<div class="step ${cls}"><div class="n">${s.name}</div>` +
           `<div class="t">${note}</div>${leg}</div>`;
  }).join('');
}

function renderCompare(cmd) {
  const lines = [];
  if (!cmd) { $('cmdCmp').innerHTML = '还没有下发过指令。'; return; }
  lines.push(`<div>指令 <b>${cmd.id}</b> · 状态 <b>${cmd.status}</b></div>`);
  if (cmd.sample_count !== null && cmd.sample_count !== undefined) {
    lines.push(`<div>本次新采集入库 <b>${cmd.sample_count}</b> 条` +
               `（id ${cmd.first_reading_id ?? '—'} ~ ${cmd.last_reading_id ?? '—'}）</div>`);
  }
  /* 设备给的原因/备注。失败时它是"为什么失败"的唯一线索；
   * 部分送达时它说明"完成得不干净"。有就一定要显示出来 ——
   * 只看到一个红色的"失败"却不知道为什么，等于白跑一趟。 */
  if (cmd.note) {
    const bad = (cmd.status === 'failed');
    lines.push(`<div style="color:${bad ? '#a32d2d' : '#854f0b'}">` +
               `设备备注：<b>${cmd.note}</b></div>`);
  }
  const legs = cmd.legs_ms || {};
  if (legs.total_ms !== undefined) {
    lines.push(`<div>端到端耗时 <b>${legs.total_ms} ms</b></div>`);
  }
  if (CMD.totalBefore !== null) {
    lines.push(`<div>下发前库里 <b>${CMD.totalBefore}</b> 条（刷新不会让它变多）</div>`);
  }
  $('cmdCmp').innerHTML = lines.join('');
}

async function fetchTotal() {
  try {
    const d = await getJSON(API_BASE + '/api/devices');
    return (typeof d.total === 'number') ? d.total
         : (d.devices || []).reduce((s, x) => s + (x.count || 0), 0);
  } catch (e) { return null; }
}

async function pollCommand() {
  if (!CMD.id) return;
  try {
    const cmd = await getJSON(API_BASE + '/api/command/' + encodeURIComponent(CMD.id));
    CMD.last = cmd;
    renderTimeline(cmd);
    renderCompare(cmd);
    if (TERMINAL.includes(cmd.status)) {
      clearInterval(CMD.timer);
      CMD.timer = null;
      ['btnCmd', 'btnPause'].forEach(id => { const b = $(id); if (b) b.disabled = false; });
      const after = await fetchTotal();
      if (after !== null && CMD.totalBefore !== null) {
        const extra = `<div>下发后库里 <b>${after}</b> 条` +
          `<b style="color:${after > CMD.totalBefore ? '#1d9e75' : '#ba7517'}">` +
          `（${after > CMD.totalBefore ? '+' : ''}${after - CMD.totalBefore}）</b></div>`;
        $('cmdCmp').insertAdjacentHTML('beforeend', extra);
      }
      if (cmd.status === 'done') {
        if (cmd.type === 'pause') {
          $('cmdHint').innerHTML = '周期上报已暂停。板子改用 <code>/api/poll</code> 心跳保持' +
            '命令通道 —— 现在再点「重新采集」，新出现的记录<b>只可能来自指令</b>，' +
            '这就是最硬的证据。';
        } else if (cmd.type === 'resume') {
          $('cmdHint').innerHTML = '周期上报已恢复，板子重新每秒上报。';
        } else {
          $('cmdHint').innerHTML =
            `已让设备完成一次新采集：${cmd.sample_count} 条在 ${cmd.legs_ms?.total_ms} ms 内入库。` +
            ' 表格里带 <span class="tag cmd">指令</span> 标记的行就是这次新采的。';
        }
      } else if (cmd.status === 'failed') {
        /* 失败与超时必须分开说，因为它们要查的地方完全不同：
         *   失败 —— 设备明确回报"我试了，没干成"（原因在 cmd.note 里）
         *   超时 —— 服务器什么回音都没听到，只能判定"不知道"
         * 混成一句"没成功"，排查时就会在错误的方向上浪费时间。 */
        $('cmdHint').innerHTML =
          `<b style="color:#a32d2d">设备回报执行失败</b>：` +
          `${cmd.note || '（设备没有给出原因）'}` +
          ' —— 这是<b>设备亲口说的</b>，说明指令确实送到了，问题出在执行这一端。';
      } else {
        $('cmdHint').innerHTML =
          `指令 ${cmd.status}（${cmd.timeout_stage || ''}）—— 板子当前不在线或未响应。` +
          ' 注意：<b>超时只说明这一条没走完，不能据此断定硬件坏了</b>。';
      }
      tick();                       // 立刻刷新一次表格，不用等下一个周期
    }
  } catch (e) {
    // 服务器偶发卡顿，下个周期再试，不打断
  }
}

/* 统一的指令下发。采集指令与控制指令（暂停/恢复）走**完全相同**的一条通道、
 * 同一套四阶段时间线 —— 这不是偷懒，本身就是要展示的事实：
 * 暂停之后命令通道还在，靠的就是这条搭车下行通道没被一起停掉。
 * 如果给暂停单独做一套界面，反而看不清"通道保持"这件事。 */
async function issueCommand(type, extra = {}) {
  const dev = ($('dev').textContent || '').replace('device: ', '').trim();
  if (!dev || dev === '—') {
    $('cmdHint').textContent = '还没识别到设备，等板子上报一次再试。';
    return false;
  }
  const btns = ['btnCmd', 'btnPause'].map(id => $(id)).filter(Boolean);
  btns.forEach(b => { b.disabled = true; });
  $('cmdHint').textContent = '正在向服务器受理指令…';

  CMD.totalBefore = await fetchTotal();   // 记下"下发前"的条数，用于对比
  try {
    const r = await fetch(API_BASE + '/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ device_id: dev, type }, extra)),
    });
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      let payload = null;
      try {
        payload = await r.json();
        msg = payload.error || msg;
        if (payload.allowed) msg += '（允许的类型：' + payload.allowed.join(' / ') + '）';
      } catch (_) { /* 服务器没返回 JSON，就用状态码 */ }

      /* 409 = 上一次同类指令还没走完。这不是错误，而是"你点重复了"，
       * 所以别丢一句红字就完事 —— 直接把界面接到那条在途指令上继续跟踪。
       * 用户在意的不是"被拒绝了"，而是"我这一下到底有没有生效"。 */
      if (r.status === 409 && payload && payload.existing_request_id) {
        CMD.id = payload.existing_request_id;
        $('cmdHint').innerHTML =
          `上一次「${type === 'recollect' ? '重新采集' : '周期上报'}」还没走完，` +
          `已改为继续跟踪 <code>${CMD.id}</code>（当前 ${payload.existing_status}）——` +
          `重复点击不会多采一次，也不会把上一次挤掉。`;
        renderTimeline(null);
        await pollCommand();
        if (CMD.timer) clearInterval(CMD.timer);
        CMD.timer = setInterval(pollCommand, 500);
        return true;
      }
      throw new Error(msg);
    }
    const j = await r.json();
    CMD.id = j.request_id;
    const what = type === 'recollect'
      ? `采 ${j.params.samples} 条、间隔 ${j.params.interval_ms} ms`
      : (type === 'pause' ? '暂停周期上报' : '恢复周期上报');
    $('cmdHint').innerHTML = `已受理指令 <code>${CMD.id}</code>（${what}）：` +
      '等板子下一次上报/心跳来取走。';
    renderTimeline(null);
    await pollCommand();
    if (CMD.timer) clearInterval(CMD.timer);
    CMD.timer = setInterval(pollCommand, 500);   // 500ms 一次，看清每步推进
    return true;
  } catch (e) {
    btns.forEach(b => { b.disabled = false; });
    $('cmdHint').textContent = '下发失败：' + e.message;
    return false;
  }
}

$('btnCmd').addEventListener('click', () => {
  const [samples, interval] = $('cmdPlan').value.split(',').map(Number);
  issueCommand('recollect', { samples: samples, interval_ms: interval });
});

/* 暂停/恢复：按当前状态决定发哪个动作，避免连点两次都发 pause */
$('btnPause').addEventListener('click', () => {
  issueCommand(PAUSED ? 'resume' : 'pause');
});

renderTimeline(null);

/* ---------- 下载历史数据 ----------
 * 直接指向服务端导出接口，让浏览器按 Content-Disposition 自己下载，
 * 不用 fetch 回来再拼 Blob —— 几万条也不会先把内存撑起来。 */
$('btnCsv').addEventListener('click', () => {
  const v = $('csvRange').value;
  const q = (v === 'all') ? '' : '?' + v;   // 'all' = 不传参数 = 服务端默认上限
  window.location.href = API_BASE + '/api/export.csv' + q;
});

/* 顺带显示库里累计多少条，让"下载的是什么"一目了然 */
(async function loadCount() {
  try {
    const d = await getJSON(API_BASE + '/api/devices');
    const n = (typeof d.total === 'number') ? d.total
            : (d.devices || []).reduce((s, x) => s + (x.count || 0), 0);
    $('csvInfo').textContent = '累计 ' + n.toLocaleString('zh-CN') + ' 条'
      + (d.max_rows ? '（上限 ' + d.max_rows.toLocaleString('zh-CN') + '）' : '');
  } catch (e) { /* 拿不到就不显示，不影响下载 */ }
})();

renderLegend();
tick();
setInterval(tick, 1000);
/* 切回前台立即补一次，不用等下一个 1 秒周期 */
document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
