(function(root) {
  'use strict';

  class ReadingSpeechPlayer {
    constructor(createAudio, onChange, browserAudio = null) {
      this.createAudio = createAudio;
      this.browserAudio = browserAudio;
      this.onChange = onChange;
      this.audio = null;
      this.clips = [];
      this.index = 0;
      this.token = 0;
      this.playRequest = 0;
      this.state = 'idle';
      this.sourceState = 'done';
      this.durations = new Map();
      this.wantsPlay = false;
      this.active = false;
      this.at = 0;
    }
    emit(state = this.state) {
      this.state = state;
      this.onChange(state, this.clips[this.index], this.index, this.clips.length);
    }
    release() {
      this.token++;
      this.playRequest++;
      if (this.audio) {
        this.audio.onended = this.audio.onerror = this.audio.onloadedmetadata = this.audio.ontimeupdate = null;
        this.audio.pause();
        this.audio.src = '';
        this.audio = null;
      }
    }
    stop() { this.active = this.wantsPlay = false; this.release(); this.emit('idle'); }
    start(clips, sourceState = 'done') {
      this.release();
      this.clips = clips;
      this.sourceState = sourceState;
      this.index = this.at = 0;
      this.active = this.wantsPlay = true;
      this.usingFallback = false;
      this.durations.clear();
      return this.playClip();
    }
    update(clips, sourceState) {
      this.clips = clips;
      this.sourceState = sourceState;
      if (!this.active) return;
      if (!this.audio && this.wantsPlay) this.playClip();
      else this.emit();
    }
    setDuration(url, seconds) {
      if (Number.isFinite(seconds) && seconds > 0) {
        this.durations.set(url, seconds);
        if (this.active) this.emit();
      }
    }
    progress() {
      let total = 0, before = 0;
      for (let i = 0; i < this.clips.length; i++) {
        const clip = this.clips[i], duration = this.durations.get(clip.url);
        if (clip.ready === false || !duration) break;
        if (i < this.index) before += duration;
        total += duration;
      }
      return { total, current: Math.min(total, before + (Number(this.audio?.currentTime) || this.at || 0)) };
    }
    async seek(seconds) {
      const total = this.progress().total;
      if (!total || !this.active) return;
      let at = Math.max(0, Math.min(Number(seconds) || 0, total - 0.01)), index = 0;
      while (index < this.clips.length - 1 && at >= this.durations.get(this.clips[index].url)) {
        at -= this.durations.get(this.clips[index++].url);
      }
      this.index = index;
      this.at = at;
      await this.playClip();
    }
    async playClip() {
      this.release();
      if (!this.active) return;
      const clip = this.clips[this.index];
      if (!clip || clip.ready === false) {
        this.emit(!this.wantsPlay ? 'paused' : this.sourceState === 'running' ? 'waiting' :
          !clip && this.sourceState === 'done' ? 'ended' : 'blocked');
        return;
      }
      const token = this.token;
      const audio = this.audio = (this.usingFallback ? this.browserAudio : this.createAudio)();
      audio.onloadedmetadata = () => {
        if (token !== this.token) return;
        if (this.at) audio.currentTime = Number.isFinite(audio.duration) ? Math.min(this.at, Math.max(0, audio.duration - .01)) : this.at;
        this.setDuration(clip.url, audio.duration);
      };
      audio.ontimeupdate = () => {
        if (token !== this.token) return;
        this.at = Number(audio.currentTime) || 0;
        this.setDuration(clip.url, audio.duration);
        this.emit();
      };
      audio.onended = () => {
        if (token !== this.token) return;
        this.index++;
        this.at = 0;
        this.playClip();
      };
      const failed = () => {
        if (token !== this.token) return;
        this.at = Number(audio.currentTime) || this.at;
        if (this.browserAudio && !this.usingFallback) {
          this.usingFallback = true;
          this.playClip();
        } else {
          this.wantsPlay = false;
          this.release();
          this.emit('error');
        }
      };
      audio.onerror = failed;
      audio.src = clip.url;
      if (!this.wantsPlay) { audio.load?.(); this.emit('paused'); return; }
      await this.playAudio(failed);
    }
    pause() {
      this.wantsPlay = false;
      this.playRequest++;
      this.audio?.pause();
      this.emit('paused');
    }
    async resume() {
      if (!this.active) return;
      this.wantsPlay = true;
      if (!this.audio) return this.playClip();
      await this.playAudio(this.audio.onerror);
    }
    async playAudio(failed) {
      const token = this.token, request = ++this.playRequest;
      this.emit('loading');
      try {
        await this.audio.play();
        if (token === this.token && request === this.playRequest) this.emit('playing');
      } catch (error) {
        if (token !== this.token || request !== this.playRequest) return;
        if (error.name === 'NotAllowedError') this.pause();
        else failed();
      }
    }
  }

  if (typeof module === 'object' && module.exports) module.exports = { ReadingSpeechPlayer };
  if (!root || !root.document) return;
  const $ = id => document.getElementById(id);
  let context = null, plan = null, epoch = 0, pollTimer = null, requestId = 0;
  let busy = false, generating = false, listening = false, signature = '';
  let playingPlan = null, probing = false, probeToken = 0, dragging = false;
  const panel = $('readingSpeechPanel'), select = $('readingNarrator');
  const start = $('readingSpeechStart'), status = $('readingSpeechStatus');
  const bar = $('readingSpeechBar'), pause = $('readingSpeechPause'), seek = $('readingSpeechSeek');
  const time = seconds => `${Math.floor((seconds || 0) / 60)}:${String(Math.floor((seconds || 0) % 60)).padStart(2, '0')}`;
  let highlighted = null;
  function highlight(clip) {
    if ((clip?.paragraph ?? null) === highlighted) return;
    highlighted = clip?.paragraph ?? null;
    document.querySelectorAll('.reading-speaking').forEach(el => el.classList.remove('reading-speaking'));
    if (!clip) return;
    const el = document.querySelector(`#readerContent [data-p="${clip.paragraph}"]`);
    if (el) { el.classList.add('reading-speaking'); el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
  }
  function progressUI() {
    const progress = player.progress();
    seek.max = progress.total;
    seek.disabled = progress.total === 0;
    if (!dragging) {
      seek.value = progress.current;
      $('readingSpeechCurrent').textContent = time(progress.current);
    }
    const allKnown = player.clips.every(c => c.ready !== false && player.durations.has(c.url));
    $('readingSpeechDuration').textContent = `${allKnown ? '' : '已就绪 '}${time(progress.total)}`;
    const p = playingPlan;
    $('readingSpeechSynthesis').textContent = p ? `${p.job?.state === 'running' ? '合成中' : p.missing_count ? '待补全' : '已合成'} ${p.clips.filter(c => c.ready).length}/${p.clips.length} 段` : '';
  }
  const player = new ReadingSpeechPlayer(() => root.createTtsAudio(''), (state, clip, index, total) => {
    if (state === 'idle') { bar.hidden = true; highlight(null); return; }
    bar.hidden = false;
    pause.hidden = state === 'ended';
    pause.dataset.playing = String(player.wantsPlay);
    pause.title = player.wantsPlay ? '暂停' : '继续';
    pause.setAttribute('aria-label', pause.title);
    $('readingSpeechRetry').hidden = !playingPlan?.missing_count || playingPlan?.job?.state === 'running';
    let text;
    if (state === 'ended') { text = '本章朗读完毕'; highlight(null); }
    else if (state === 'error') { text = '音频加载失败，点击继续重试'; highlight(null); }
    else if (state === 'blocked') { text = playingPlan?.job?.error || '语音尚未合成完整，请补全语音后继续'; highlight(null); }
    else if (state === 'waiting') text = '等待下一段语音，合成好会自动继续…';
    else {
      text = `${state === 'paused' ? '已暂停 · ' : ''}${clip ? `${clip.name} · ${{annotation:'批注', summary:'读后感', body:'正文'}[clip.kind] || '正文'} ${index + 1}/${total}` : '等待后续语音'}`;
      highlight(clip);
    }
    $('readingSpeechNow').textContent = text;
    $('readingSpeechNow').title = text;
    progressUI();
  }, typeof root.AionTtsAudio?.prepareAudio === 'function' ? () => new root.Audio() : null);
  function preferenceKey() { return `reading-narrator:${context.bookId}:${context.chapter}`; }
  function url() { return `/api/books/${encodeURIComponent(context.bookId)}/chapters/${context.chapter}/speech`; }
  function sourceState(p) { return p.job?.state === 'running' ? 'running' : !p.missing_count ? 'done' : 'error'; }
  function schedulePoll() {
    clearTimeout(pollTimer);
    const token = epoch;
    pollTimer = setTimeout(() => { if (token === epoch) refresh(); }, 1000);
  }
  // Metadata probing runs independently: a slow later clip never delays the first play.
  async function probeDurations() {
    if (probing || !listening) return;
    probing = true;
    const token = probeToken;
    try {
      for (const clip of player.clips) {
        if (token !== probeToken || !listening) break;
        if (!clip.ready || player.durations.has(clip.url)) continue;
        await new Promise(resolve => {
          const audio = new root.Audio();
          audio.preload = 'metadata';
          const timer = setTimeout(done, 8000);
          function done() {
            clearTimeout(timer);
            audio.onloadedmetadata = audio.onerror = null;
            audio.src = '';
            resolve();
          }
          audio.onloadedmetadata = () => {
            if (token === probeToken) player.setDuration(clip.url, audio.duration);
            done();
          };
          audio.onerror = done;
          audio.src = clip.url;
        });
      }
    } finally { if (token === probeToken) probing = false; }
  }
  function render() {
    select.disabled = busy || generating;
    start.disabled = busy || !plan || !!plan.missing_voices.length || !plan.clips.length;
    start.textContent = generating ? '边合成边朗读' : plan?.job?.state === 'error' ? '补全并继续朗读' : '开始朗读';
    if (!plan) return;
    const counts = `正文 ${plan.body_chars} 字 · 批注 ${plan.annotation_chars} 字 · 读后感 ${plan.summary_chars || 0} 字`;
    if (plan.missing_voices.length) status.textContent = `${counts}\n请先在群聊设置中配置${plan.missing_voices.join('、')}的音色。`;
    else if (!plan.clips.length) status.textContent = '本章没有可朗读的文字。';
    else if (generating) status.textContent = `${counts}\n正在合成 ${plan.job.completed}/${plan.job.total} 段，可立即播放已就绪语音。`;
    else if (plan.job?.state === 'error') status.textContent = `${counts}\n${plan.job.error}（还需合成 ${plan.missing_chars} 字）`;
    else status.textContent = `${counts}\n${plan.missing_count ? `还需合成 ${plan.missing_chars} 字，确认后边合成边播放。` : '本章语音已备好，可以直接播放。'}`;
  }
  async function refresh(latest = false) {
    if (!context) return false;
    const token = epoch, request = ++requestId;
    try {
      const pin = !latest && signature ? `&signature=${signature}` : '';
      const result = await root.api('GET', `${url()}?narrator=${select.value}${pin}`);
      if (token !== epoch || request !== requestId) return false;
      plan = result;
      generating = plan.job?.state === 'running';
      render();
      if (listening && signature === plan.signature) {
        playingPlan = plan;
        player.update(plan.clips, sourceState(plan));
        probeDurations();
      }
      if (generating) schedulePoll();
      return true;
    } catch (error) {
      if (token !== epoch || request !== requestId) return false;
      status.textContent = error.message;
      if (error.status === 409) {
        generating = false;
        if (playingPlan) {
          playingPlan = {...playingPlan, job: {state:'error', error:error.message}};
          player.update(player.clips, 'error');
        }
      } else if (generating) {
        if (listening) $('readingSpeechSynthesis').textContent = '连接中断，正在重新获取合成进度…';
        schedulePoll();
      }
      return false;
    }
  }
  function listen(p, continuePlayback = false) {
    const keepPosition = continuePlayback && listening && signature === p.signature;
    signature = p.signature;
    playingPlan = p;
    listening = true;
    panel.hidden = true;
    if (keepPosition) { player.update(p.clips, sourceState(p)); player.resume(); }
    else { probeToken++; probing = false; player.start(p.clips, sourceState(p)); }
    probeDurations();
    if (p.job?.state === 'running') schedulePoll();
  }
  async function begin() {
    if (busy || !context) return;
    const token = epoch;
    busy = true;
    render();
    try {
      if (!await refresh(true)) return;
      if (token !== epoch || !plan || plan.missing_voices.length || !plan.clips.length) return;
      if (generating || !plan.missing_count) { listen(plan, generating); return; }
      const approved = root.confirm(`本章正文共 ${plan.body_chars} 字，批注 ${plan.annotation_chars} 字，读后感 ${plan.summary_chars || 0} 字。\n本次需合成 ${plan.missing_chars} 字（已有语音会复用）。\n正文由${select.selectedOptions[0].textContent}朗读，批注和读后感使用各自作者的声音。\n是否开始合成并逐段播放？`);
      if (!approved) return;
      const result = await root.api('POST', url(), { narrator: select.value, signature: plan.signature });
      if (token !== epoch) return;
      plan = result;
      generating = plan.job?.state === 'running';
      listen(plan, true);
      await refresh();
    } catch (error) {
      if (token === epoch) { status.textContent = error.message; root.showToast(error.message); }
    } finally { if (token === epoch) { busy = false; render(); } }
  }
  function reset() {
    epoch++; requestId++; probeToken++;
    clearTimeout(pollTimer);
    context = plan = playingPlan = null;
    signature = '';
    busy = generating = listening = probing = false;
    player.stop();
    panel.hidden = true;
  }
  root.ReadingSpeech = {
    reset,
    setChapter(bookId, chapter, names) {
      reset();
      context = { bookId, chapter };
      select.replaceChildren(...['aion', 'connor'].map((id, index) => new Option(names[index], id)));
      try { select.value = localStorage.getItem(preferenceKey()) || 'aion'; } catch (_) {}
      if (!select.value) select.value = 'aion';
    },
    open() {
      if (!context) return;
      panel.hidden = false;
      status.textContent = '正在检查本章语音…';
      start.disabled = true;
      refresh();
    }
  };
  $('readingSpeechSettings').onclick = root.ReadingSpeech.open;
  $('readingSpeechRetry').onclick = root.ReadingSpeech.open;
  $('readingSpeechClose').onclick = () => { panel.hidden = true; };
  panel.onclick = event => { if (event.target === panel) panel.hidden = true; };
  document.addEventListener('keydown', event => { if (event.key === 'Escape') panel.hidden = true; });
  select.onchange = () => {
    player.stop();
    listening = false; signature = ''; probeToken++; probing = false;
    plan = null;
    try { localStorage.setItem(preferenceKey(), select.value); } catch (_) {}
    status.textContent = '正在检查本章语音…';
    render(); refresh();
  };
  start.onclick = begin;
  pause.onclick = () => player.wantsPlay ? player.pause() : player.resume();
  seek.oninput = () => { dragging = true; $('readingSpeechCurrent').textContent = time(Number(seek.value)); };
  seek.onchange = () => { dragging = false; player.seek(Number(seek.value)); };
  $('readingSpeechStop').onclick = () => { listening = false; probeToken++; probing = false; player.stop(); };
  root.addEventListener('pagehide', reset);
})(typeof window !== 'undefined' ? window : null);
