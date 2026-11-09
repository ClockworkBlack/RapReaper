'use strict';

(function () {
  var $ = function (id) { return document.getElementById(id); };

  // The program starts this page with a one-time key after the "#". Keep it for reloads
  // of this tab and take it out of the address bar.
  var token = (location.hash || '').slice(1);
  try {
    if (token) { sessionStorage.setItem('vr-token', token); }
    else { token = sessionStorage.getItem('vr-token') || ''; }
  } catch (e) { /* storage may be unavailable; the key from the address still works */ }
  if (location.hash) { history.replaceState(null, '', location.pathname); }

  var state = {
    page: '',
    track: null,      // {id, name, seconds, peaks, ...}
    start: 0,
    end: 0,
    busy: false,
    pollTimer: 0,
    shownJob: ''
  };

  var alertBox = $('alert');
  function showError(message) {
    alertBox.textContent = message;
    alertBox.hidden = false;
    alertBox.scrollIntoView({ block: 'nearest' });
  }
  function clearError() { alertBox.hidden = true; alertBox.textContent = ''; }

  function api(method, path, body) {
    var init = { method: method, headers: { 'X-VR-Token': token } };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (res) {
      if (res.status === 204) { return null; }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { throw new Error(data.error || ('The program answered with an error (' + res.status + ').')); }
        return data;
      });
    }, function () {
      $('offline').classList.add('lost');
      $('offline').lastChild.textContent = 'The program is not running';
      throw new Error('The program is no longer running. Start Vocal Remover again, then reopen this window.');
    });
  }

  // ---- time helpers -------------------------------------------------------

  function formatTime(sec) {
    sec = Math.max(0, sec);
    var tenths = Math.round(sec * 10);
    var m = Math.floor(tenths / 600);
    var s = (tenths - m * 600) / 10;
    return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1);
  }

  function spokenTime(sec) {
    var m = Math.floor(sec / 60);
    var s = Math.round((sec - m * 60) * 10) / 10;
    return (m ? m + (m === 1 ? ' minute ' : ' minutes ') : '') + s + ' seconds';
  }

  function parseTime(text) {
    text = String(text).trim().replace(',', '.');
    if (!text) { return NaN; }
    var parts = text.split(':');
    if (parts.length > 3) { return NaN; }
    var total = 0;
    for (var i = 0; i < parts.length; i++) {
      if (!/^\d*\.?\d*$/.test(parts[i].trim()) || parts[i].trim() === '' || parts[i].trim() === '.') { return NaN; }
      total = total * 60 + parseFloat(parts[i]);
    }
    return total;
  }

  function roughDuration(sec) {
    if (sec < 50) { return 'less than a minute'; }
    var m = Math.round(sec / 60);
    return 'about ' + m + (m === 1 ? ' minute' : ' minutes');
  }

  // ---- loading a song -----------------------------------------------------

  var drop = $('drop'), fileInput = $('file');

  $('choose').addEventListener('click', function () { fileInput.click(); });
  $('change').addEventListener('click', function () {
    if (state.busy) { showError('A song is being processed. Wait for it to finish or cancel it first.'); return; }
    fileInput.click();
  });
  fileInput.addEventListener('change', function () {
    if (fileInput.files && fileInput.files[0]) { loadFile(fileInput.files[0]); }
    fileInput.value = '';
  });

  // Dropping a file anywhere on the window loads it (and never navigates away).
  window.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('over'); });
  window.addEventListener('dragleave', function (e) { if (!e.relatedTarget) { drop.classList.remove('over'); } });
  window.addEventListener('drop', function (e) {
    e.preventDefault();
    drop.classList.remove('over');
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { loadFile(f); }
  });

  function loadFile(file) {
    if (state.busy) { showError('A song is being processed. Wait for it to finish or cancel it first.'); return; }
    if (file.size > 1024 * 1024 * 1024) {
      showError('This file is larger than 1 GB, which is more than the program can load. Cut it into shorter files first.');
      return;
    }
    clearError();
    stopPlayback();
    $('editor').hidden = true;
    $('results').hidden = true;
    $('progress').hidden = true;
    drop.hidden = false;
    $('choose').disabled = true;
    $('loading').hidden = false;
    $('load-fill').style.width = '0%';
    $('load-text').textContent = 'Opening ' + file.name;

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?name=' + encodeURIComponent(file.name));
    xhr.setRequestHeader('X-VR-Token', token);
    xhr.upload.addEventListener('progress', function (e) {
      if (e.lengthComputable) { $('load-fill').style.width = Math.round(80 * e.loaded / e.total) + '%'; }
    });
    xhr.upload.addEventListener('load', function () {
      $('load-fill').style.width = '85%';
      $('load-text').textContent = 'Reading the audio';
    });
    var finish = function () { $('choose').disabled = false; $('loading').hidden = true; };
    xhr.addEventListener('load', function () {
      finish();
      var data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* handled below */ }
      if (xhr.status !== 200) {
        showError(data.error || 'The song could not be opened.');
        if (state.track) { showEditor(); }
        return;
      }
      setTrack(data);
    });
    xhr.addEventListener('error', function () {
      finish();
      showError('The program is no longer running. Start Vocal Remover again, then reopen this window.');
    });
    xhr.send(file);
  }

  function setTrack(track) {
    state.track = track;
    state.start = 0;
    state.end = track.seconds;
    state.shownJob = '';
    $('song-name').textContent = track.name;
    var ch = track.channels === 1 ? 'mono' : (track.channels === 2 ? 'stereo' : track.channels + ' channels');
    $('song-meta').textContent = track.format + ' (' + track.detail + '), ' + (track.rate / 1000) + ' kHz, ' + ch + ', ' + formatTime(track.seconds);
    $('orig-audio').src = '/api/preview/original?t=' + encodeURIComponent(token) + '&id=' + track.id;
    $('results').hidden = true;
    showEditor();
    syncSelection();
  }

  function showEditor() {
    drop.hidden = true;
    $('editor').hidden = false;
    resizeCanvas();
  }

  // ---- waveform and selection ----------------------------------------------

  var wrap = $('wave-wrap'), canvas = $('wave'), ctx = canvas.getContext('2d');
  var handleStart = $('handle-start'), handleEnd = $('handle-end');
  var MIN_SPAN = 0.1;

  function resizeCanvas() {
    var w = wrap.clientWidth, h = wrap.clientHeight;
    if (!w || !h) { return; }
    var ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
    drawWave();
    placeSelection();
  }
  window.addEventListener('resize', resizeCanvas);

  function drawWave() {
    var t = state.track;
    if (!t) { return; }
    var w = canvas.width, h = canvas.height, peaks = t.peaks, n = peaks.length;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f4a93b';
    var mid = h / 2;
    for (var x = 0; x < w; x++) {
      var lo = Math.floor(x * n / w), hi = Math.max(lo + 1, Math.floor((x + 1) * n / w));
      var p = 0;
      for (var i = lo; i < hi && i < n; i++) { if (peaks[i] > p) { p = peaks[i]; } }
      var bar = Math.max(1, p * (h - 8));
      ctx.fillRect(x, mid - bar / 2, 1, bar);
    }
  }

  function placeSelection() {
    var t = state.track;
    if (!t) { return; }
    var w = wrap.clientWidth;
    var a = state.start / t.seconds * w, b = state.end / t.seconds * w;
    handleStart.style.left = a + 'px';
    handleEnd.style.left = b + 'px';
    $('shade-left').style.width = a + 'px';
    $('shade-right').style.width = Math.max(0, w - b) + 'px';
    [[handleStart, state.start], [handleEnd, state.end]].forEach(function (pair) {
      pair[0].setAttribute('aria-valuemin', '0');
      pair[0].setAttribute('aria-valuemax', String(Math.round(t.seconds * 10) / 10));
      pair[0].setAttribute('aria-valuenow', String(Math.round(pair[1] * 10) / 10));
      pair[0].setAttribute('aria-valuetext', spokenTime(pair[1]));
    });
  }

  function syncSelection() {
    var t = state.track;
    if (!t) { return; }
    $('from').value = formatTime(state.start);
    $('to').value = formatTime(state.end);
    var whole = state.start <= 0.0005 && state.end >= t.seconds - 0.0005;
    $('span-length').textContent = whole ? 'Whole song, ' + formatTime(t.seconds) : 'Selected ' + formatTime(state.end - state.start);
    $('go-hint').textContent = (whole ? 'Processes the whole song' : 'Processes only the selected part') +
      ' and makes two files: the instrumental and the vocals.';
    placeSelection();
  }

  function setSpan(start, end) {
    var t = state.track;
    start = Math.min(Math.max(0, start), t.seconds);
    end = Math.min(Math.max(0, end), t.seconds);
    if (end < start) { var tmp = start; start = end; end = tmp; }
    if (end - start < MIN_SPAN) {
      if (start + MIN_SPAN <= t.seconds) { end = start + MIN_SPAN; } else { start = Math.max(0, end - MIN_SPAN); }
    }
    state.start = start;
    state.end = end;
    syncSelection();
  }

  function timeAt(clientX) {
    var rect = wrap.getBoundingClientRect();
    var f = (clientX - rect.left) / rect.width;
    return Math.min(1, Math.max(0, f)) * state.track.seconds;
  }

  var drag = null; // {kind: 'start' | 'end' | 'new', anchor}
  wrap.addEventListener('pointerdown', function (e) {
    if (!state.track || state.busy || e.button !== 0) { return; }
    var kind = e.target === handleStart ? 'start' : (e.target === handleEnd ? 'end' : 'new');
    drag = { kind: kind, anchor: timeAt(e.clientX), moved: false };
    wrap.setPointerCapture(e.pointerId);
    if (kind !== 'new') { e.target.focus(); }
    e.preventDefault();
  });
  wrap.addEventListener('pointermove', function (e) {
    if (!drag) { return; }
    var t = timeAt(e.clientX);
    if (drag.kind === 'start') { setSpan(Math.min(t, state.end - MIN_SPAN), state.end); }
    else if (drag.kind === 'end') { setSpan(state.start, Math.max(t, state.start + MIN_SPAN)); }
    else if (Math.abs(t - drag.anchor) * wrap.clientWidth / state.track.seconds > 3) { drag.moved = true; setSpan(drag.anchor, t); }
  });
  var endDrag = function (e) {
    if (!drag) { return; }
    // A plain click (no drag) moves the listening position there.
    if (drag.kind === 'new' && !drag.moved) { seekTo(drag.anchor); }
    drag = null;
    try { wrap.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
  };
  wrap.addEventListener('pointerup', endDrag);
  wrap.addEventListener('pointercancel', endDrag);

  function handleKeys(which) {
    return function (e) {
      if (!state.track || state.busy) { return; }
      var step = e.shiftKey ? 0.1 : 1;
      var value = which === 'start' ? state.start : state.end;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { value -= step; }
      else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { value += step; }
      else if (e.key === 'PageDown') { value -= 10; }
      else if (e.key === 'PageUp') { value += 10; }
      else if (e.key === 'Home') { value = 0; }
      else if (e.key === 'End') { value = state.track.seconds; }
      else { return; }
      e.preventDefault();
      if (which === 'start') { setSpan(Math.min(value, state.end - MIN_SPAN), state.end); }
      else { setSpan(state.start, Math.max(value, state.start + MIN_SPAN)); }
    };
  }
  handleStart.addEventListener('keydown', handleKeys('start'));
  handleEnd.addEventListener('keydown', handleKeys('end'));

  function timeField(input, which) {
    var commit = function () {
      if (!state.track) { return; }
      var v = parseTime(input.value);
      if (isNaN(v)) {
        showError('"' + input.value + '" is not a time. Use minutes:seconds, for example 1:07.5');
        syncSelection();
        return;
      }
      clearError();
      v = Math.min(v, state.track.seconds);
      if (which === 'start') { setSpan(v, Math.max(state.end, Math.min(v + MIN_SPAN, state.track.seconds))); }
      else { setSpan(Math.min(state.start, Math.max(0, v - MIN_SPAN)), v); }
    };
    input.addEventListener('change', commit);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { commit(); input.blur(); } });
  }
  timeField($('from'), 'start');
  timeField($('to'), 'end');

  $('whole').addEventListener('click', function () {
    if (state.track && !state.busy) { setSpan(0, state.track.seconds); }
  });

  // ---- listening to the original --------------------------------------------

  var orig = $('orig-audio'), playBtn = $('play'), playhead = $('playhead'), raf = 0;

  function stopPlayback() {
    orig.pause();
    playBtn.setAttribute('aria-pressed', 'false');
    playBtn.textContent = 'Play selection';
    playhead.hidden = true;
    cancelAnimationFrame(raf);
  }

  function tick() {
    if (orig.paused || !state.track) { return; }
    if (orig.currentTime >= state.end) { stopPlayback(); return; }
    playhead.hidden = false;
    playhead.style.left = (orig.currentTime / state.track.seconds * wrap.clientWidth) + 'px';
    raf = requestAnimationFrame(tick);
  }

  function startPlayback(from) {
    orig.currentTime = from;
    var p = orig.play();
    playBtn.setAttribute('aria-pressed', 'true');
    playBtn.textContent = 'Stop';
    if (p && p.catch) { p.catch(function () { stopPlayback(); showError('This browser could not play the song preview. Processing still works.'); }); }
    raf = requestAnimationFrame(tick);
  }

  function seekTo(time) {
    if (!state.track) { return; }
    if (!orig.paused) { orig.currentTime = time; return; }
    startPlayback(time);
  }

  playBtn.addEventListener('click', function () {
    if (!state.track) { return; }
    if (!orig.paused) { stopPlayback(); return; }
    startPlayback(state.start);
  });
  orig.addEventListener('ended', stopPlayback);

  // ---- modes and the graphics processor ---------------------------------------

  var defaultMode = 'mel';

  function renderModes(modes) {
    var box = $('modes');
    Array.prototype.forEach.call(box.querySelectorAll('label'), function (l) { l.remove(); });
    var quickShown = false;
    modes.forEach(function (m, i) {
      if (m.quick && !quickShown) {
        quickShown = true;
        var sep = document.createElement('p');
        sep.className = 'hint';
        sep.textContent = 'Quick modes (the older MDX-Net networks, a few minutes per song, less accurate):';
        box.appendChild(sep);
      }
      var label = document.createElement('label');
      label.className = 'choice';
      var input = document.createElement('input');
      input.type = 'radio'; input.name = 'quality'; input.value = m.key;
      input.checked = (m.key === defaultMode && m.available) || false;
      input.disabled = !m.available;
      var span = document.createElement('span');
      var strong = document.createElement('strong');
      strong.textContent = m.label;
      span.appendChild(strong);
      span.appendChild(document.createTextNode(' ' + m.describe));
      if (!m.available) {
        var miss = document.createElement('span');
        miss.className = 'mode-missing';
        miss.textContent = ' (needs the model file ' + m.missing.join(', ') + ', which is not in the models folder)';
        span.appendChild(miss);
      }
      label.appendChild(input);
      label.appendChild(span);
      box.appendChild(label);
    });
    if (!document.querySelector('input[name="quality"]:checked')) {
      var first = document.querySelector('input[name="quality"]:not([disabled])');
      if (first) { first.checked = true; }
    }
  }

  var gpu = { info: null, testing: false };

  function renderGPU(info) {
    gpu.info = info;
    var box = $('gpu-box');
    if (!info) { box.hidden = true; return; }
    box.hidden = false;
    $('gpu-name').textContent = '(' + info.name + ')';
    var on = $('gpu-on');
    on.checked = !!info.on;
    on.disabled = !info.available || state.busy || gpu.testing;
    $('gpu-test').disabled = !info.available || state.busy || gpu.testing;
    var text;
    if (!info.available) {
      text = 'Not available here: ' + info.reason + '.';
    } else if (info.on) {
      text = 'On (' + (info.mode === 'unfused' ? 'one operation at a time' : 'whole graph') + ').';
    } else {
      text = 'Off.';
    }
    if (info.error) { text += ' Last problem: ' + info.error + '.'; }
    if (info.failed) { text += ' Switched off ' + info.failed + ': ' + (info.note || 'it failed during a song') + '.'; }
    else if (info.tested) { text += ' Last test ' + info.tested + ': ' + (info.note || 'no verdict') + '.'; }
    else if (info.available) { text += ' Run the test first: it tries the graphics processor on one window and switches it on if it works and is faster.'; }
    $('gpu-status').textContent = text;
    if (info.test) { renderGPUTest(info.test); }
  }

  function renderGPUTest(t) {
    if (!t) { return; }
    gpu.testing = t.state === 'running';
    $('gpu-test').disabled = gpu.testing || state.busy || !(gpu.info && gpu.info.available);
    $('gpu-on').disabled = gpu.testing || state.busy || !(gpu.info && gpu.info.available);
    $('go').disabled = gpu.testing || state.busy || !state.track;
    if (t.state === 'running') {
      $('gpu-test-text').textContent = 'Testing: ' + (t.stage || 'starting') + ' ...';
      $('gpu-report').hidden = true;
    } else if (t.state === 'error') {
      $('gpu-test-text').textContent = 'The test could not run: ' + (t.error || 'unknown problem');
    } else if (t.state === 'done') {
      $('gpu-test-text').textContent = 'Test finished (also written to Output/GPU test.txt).';
      $('gpu-report').textContent = (t.lines || []).join('\n');
      $('gpu-report').hidden = false;
    }
  }

  $('gpu-on').addEventListener('change', function () {
    var want = $('gpu-on').checked;
    $('gpu-on').disabled = true;
    api('POST', '/api/gpu', { on: want }).then(function (data) {
      renderGPU(data.gpu);
      if (data.error) { showError(data.error); }
    }, function (err) { showError(err.message); $('gpu-on').checked = !want; $('gpu-on').disabled = false; });
  });

  $('gpu-test').addEventListener('click', function () {
    clearError();
    $('gpu-test').disabled = true;
    api('POST', '/api/gpu-test').then(function () {
      gpu.testing = true;
      renderGPUTest({ state: 'running', stage: 'starting' });
      pollGPUTest();
    }, function (err) { showError(err.message); $('gpu-test').disabled = false; });
  });

  function pollGPUTest() {
    api('GET', '/api/status').then(function (st) {
      if (st.gpuTest) { renderGPUTest(st.gpuTest); }
      if (st.gpu) { renderGPU(st.gpu); }
      if (st.gpuTest && st.gpuTest.state === 'running') { setTimeout(pollGPUTest, 700); }
    }).catch(function (err) { showError(err.message); });
  }

  // ---- processing ------------------------------------------------------------

  function setBusy(busy) {
    state.busy = busy;
    ['go', 'whole', 'from', 'to', 'format', 'folder', 'browse', 'change', 'gpu-on', 'gpu-test'].forEach(function (id) { $(id).disabled = busy; });
    Array.prototype.forEach.call(document.querySelectorAll('input[name="quality"]'), function (r) { r.disabled = busy || r.dataset.missing === '1'; });
    if (!busy && gpu.info) { renderGPU(gpu.info); }
    handleStart.tabIndex = busy ? -1 : 0;
    handleEnd.tabIndex = busy ? -1 : 0;
  }

  $('go').addEventListener('click', function () {
    if (!state.track || state.busy) { return; }
    clearError();
    var picked = document.querySelector('input[name="quality"]:checked');
    if (!picked) { showError('Choose a mode first.'); return; }
    var quality = picked.value;
    setBusy(true);
    $('results').hidden = true;
    api('POST', '/api/process', {
      track: state.track.id, start: state.start, end: state.end,
      quality: quality, format: $('format').value, folder: $('folder').value
    }).then(function () {
      showProgress({ stage: 'Starting', fraction: 0 });
      $('progress').scrollIntoView({ block: 'nearest' });
      poll();
    }, function (err) {
      setBusy(false);
      showError(err.message);
    });
  });

  $('cancel').addEventListener('click', function () {
    $('cancel').disabled = true;
    api('POST', '/api/cancel').catch(function () { /* the status poll reports problems */ });
  });

  function showProgress(st) {
    $('progress').hidden = false;
    var pct = Math.round((st.fraction || 0) * 100);
    $('job-fill').style.width = pct + '%';
    $('job-bar').setAttribute('aria-valuenow', String(pct));
    var text = (st.stage || 'Working') + ', ' + pct + '%';
    if (st.remaining !== undefined) { text += ', ' + roughDuration(st.remaining) + ' left'; }
    $('job-text').textContent = text;
    document.title = pct + '% - Vocal Remover';
  }

  function poll() {
    clearTimeout(state.pollTimer);
    api('GET', '/api/status').then(function (st) {
      if (st.state === 'running') {
        showProgress(st);
        state.pollTimer = setTimeout(poll, 500);
        return;
      }
      document.title = 'Vocal Remover';
      $('progress').hidden = true;
      $('cancel').disabled = false;
      setBusy(false);
      if (st.state === 'done') { showResults(st); if (st.gpu) { renderGPU(st.gpu); } }
      else if (st.state === 'error') { showError(st.error || 'Processing failed.'); }
    }, function (err) {
      document.title = 'Vocal Remover';
      $('progress').hidden = true;
      setBusy(false);
      showError(err.message);
    });
  }

  function showResults(st) {
    if (state.shownJob === st.job) { $('results').hidden = false; return; }
    state.shownJob = st.job;
    var q = '?t=' + encodeURIComponent(token) + '&job=' + st.job;
    $('inst-audio').src = '/api/preview/instrumental' + q;
    $('voc-audio').src = '/api/preview/vocals' + q;
    $('inst-path').textContent = st.instrumental;
    $('voc-path').textContent = st.vocals;
    $('results-summary').textContent = formatTime(st.seconds) + ' of audio, ' + st.quality + ' mode, ' + st.format +
      ' WAV. Took ' + roughDuration(st.elapsed).replace('about ', '') + (st.ranOn ? ' on the ' + st.ranOn : '') + '.';
    var note = st.note || '';
    if (st.gpuFallback) { note = (note ? note + ' ' : '') + 'Graphics processor: ' + st.gpuFallback + '. The rest ran on the processor, and the graphics processor is now switched off.'; }
    $('results-note').hidden = !note;
    $('results-note').textContent = note;
    $('results').hidden = false;
    $('results').scrollIntoView({ block: 'nearest' });
  }

  // only one of the players sounds at a time
  ['inst-audio', 'voc-audio'].forEach(function (id) {
    $(id).addEventListener('play', function () {
      stopPlayback();
      ['inst-audio', 'voc-audio'].forEach(function (other) { if (other !== id) { $(other).pause(); } });
    });
  });

  $('open-folder').addEventListener('click', function () {
    api('POST', '/api/open-folder').catch(function (err) { showError(err.message); });
  });

  $('browse').addEventListener('click', function () {
    $('browse').disabled = true;
    api('POST', '/api/choose-folder', { folder: $('folder').value }).then(function (data) {
      if (data && data.folder) { $('folder').value = data.folder; }
    }, function (err) { showError(err.message); }).then(function () { $('browse').disabled = state.busy; });
  });

  // ---- start-up ------------------------------------------------------------

  window.addEventListener('pagehide', function () {
    if (state.page && navigator.sendBeacon) {
      navigator.sendBeacon('/api/bye?t=' + encodeURIComponent(token) + '&page=' + state.page);
    }
  });

  if (!token) {
    showError('Start Vocal Remover by opening the program (VocalRemover.exe on Windows, Vocal Remover.command on a Mac). This page only works when the program opens it.');
    $('choose').disabled = true;
    return;
  }

  api('POST', '/api/hello').then(function (cfg) {
    state.page = cfg.page;
    $('folder').value = cfg.outputFolder;
    $('browse').hidden = !cfg.canChooseFolder;
    if (cfg.modes) { renderModes(cfg.modes); }
    renderGPU(cfg.gpu || null);
    if (cfg.gpu && cfg.gpu.test && cfg.gpu.test.state === 'running') { gpu.testing = true; pollGPUTest(); }
    if (cfg.extensions) {
      $('file').setAttribute('accept', cfg.extensions.join(',') + ',audio/mpeg,audio/wav,audio/x-wav,audio/flac,audio/ogg');
    }
    $('about').textContent = 'Vocal Remover ' + cfg.version + ', ' + cfg.runtime + ', ' + cfg.cores + ' processor threads. Songs up to ' +
      cfg.maxMinutes + ' minutes. Nothing is uploaded or downloaded: this window talks only to the program on this computer.';
    if (cfg.track) {
      setTrack(cfg.track);
      api('GET', '/api/status').then(function (st) {
        if (st.state === 'running') { setBusy(true); showProgress(st); poll(); }
        else if (st.state === 'done') { showResults(st); }
      }).catch(function () { /* nothing to restore */ });
    }
  }).catch(function (err) {
    showError(err.message);
    $('choose').disabled = true;
  });
})();
