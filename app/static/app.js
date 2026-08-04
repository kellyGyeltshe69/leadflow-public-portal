(() => {
  const root = document.documentElement;
  const saved = localStorage.getItem('leadflow-theme');
  if (saved) root.dataset.theme = saved;

  const themeButton = document.querySelector('[data-theme-toggle]');
  themeButton?.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    localStorage.setItem('leadflow-theme', next);
  });

  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (button) {
        button.dataset.originalText = button.textContent;
        button.textContent = 'Working…';
        button.disabled = true;
      }
    });
  });

  const palette = document.querySelector('[data-command-palette]');
  const paletteInput = palette?.querySelector('input');
  const openPalette = () => {
    if (!palette) return;
    palette.hidden = false;
    paletteInput?.focus();
  };
  const closePalette = () => { if (palette) palette.hidden = true; };
  document.querySelector('[data-command-open]')?.addEventListener('click', openPalette);
  palette?.querySelector('[data-command-close]')?.addEventListener('click', closePalette);
  paletteInput?.addEventListener('input', event => {
    const query = event.target.value.toLowerCase();
    palette.querySelectorAll('a').forEach(link => {
      link.hidden = !link.textContent.toLowerCase().includes(query);
    });
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openPalette();
    }
    if (event.key === 'Escape') closePalette();
    if (!event.ctrlKey && !event.metaKey && event.key === '/' && document.activeElement?.tagName !== 'INPUT') {
      event.preventDefault();
      window.location.href = '/leads';
    }
  });

  const monitor = document.querySelector('[data-discovery-monitor]');
  if (monitor) {
    const statusUrl = monitor.dataset.statusUrl;
    let previousStatus = monitor.dataset.initialStatus || 'idle';
    let terminalReloadScheduled = false;
    const setText = (selector, value, fallback = '—') => {
      const element = monitor.querySelector(selector);
      if (element) element.textContent = value === null || value === undefined || value === '' ? fallback : String(value);
    };
    const elapsed = seconds => {
      const value = Math.max(0, Number(seconds) || 0);
      const hours = Math.floor(value / 3600);
      const minutes = Math.floor((value % 3600) / 60);
      const remaining = value % 60;
      return hours ? `${hours}h ${minutes}m ${remaining}s` : `${minutes}m ${remaining}s`;
    };
    const renderEvents = events => {
      const list = monitor.querySelector('[data-discovery-events]');
      if (!list) return;
      list.replaceChildren();
      if (!Array.isArray(events) || !events.length) {
        const item = document.createElement('li');
        item.className = 'muted';
        item.textContent = 'Waiting for discovery activity.';
        list.append(item);
        return;
      }
      events.forEach(event => {
        const item = document.createElement('li');
        const time = document.createElement('time');
        const message = document.createElement('span');
        const parsed = new Date(event.at || '');
        time.textContent = Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleTimeString();
        message.textContent = event.message || event.phase || 'Discovery activity';
        item.append(time, message);
        list.append(item);
      });
    };
    const renderJob = job => {
      if (!job) {
        setText('[data-discovery-status]', 'IDLE');
        setText('[data-discovery-message]', 'No discovery job has run yet. Use Fast Start to begin.');
        return;
      }
      const progress = job.progress || {};
      const status = job.status || 'unknown';
      const statusElement = monitor.querySelector('[data-discovery-status]');
      if (statusElement) {
        statusElement.textContent = status.toUpperCase();
        statusElement.className = `badge large ${status}`;
      }
      const dot = monitor.querySelector('[data-discovery-dot]');
      if (dot) dot.className = `activity-dot ${status}`;
      setText('[data-discovery-message]', progress.message, `Discovery job is ${status}.`);
      setText('[data-discovery-phase]', `Phase: ${(progress.phase || status).replaceAll('_', ' ')}`);
      setText('[data-discovery-elapsed]', `Elapsed: ${elapsed(job.elapsed_seconds)}`, '');
      const seen = Number(progress.candidates_seen) || 0;
      const target = Number(progress.candidate_target) || 0;
      const bar = monitor.querySelector('[data-discovery-progress]');
      if (bar) {
        bar.max = Math.max(1, target, seen);
        bar.value = seen;
      }
      setText('[data-discovery-progress-label]', `${seen} / ${target} candidates checked`);
      ['candidates_seen', 'inserted', 'duplicates_skipped', 'websites_audited', 'public_contacts_found', 'pending_approval'].forEach(name => {
        setText(`[data-discovery-stat="${name}"]`, progress[name] || 0, '0');
      });
      setText('[data-discovery-provider]', progress.provider, 'Waiting');
      setText('[data-discovery-query]', progress.current_query);
      setText('[data-discovery-business]', progress.current_business);
      setText('[data-discovery-website]', progress.current_website);
      const error = monitor.querySelector('[data-discovery-error]');
      if (error) {
        error.textContent = job.error || '';
        error.hidden = !job.error;
      }
      renderEvents(progress.events);
      if (previousStatus === 'running' && ['completed', 'failed'].includes(status) && !terminalReloadScheduled) {
        terminalReloadScheduled = true;
        window.setTimeout(() => window.location.reload(), 1500);
      }
      previousStatus = status;
    };
    const poll = async () => {
      let delay = previousStatus === 'running' ? 2000 : 10000;
      try {
        const response = await fetch(statusUrl, {
          cache: 'no-store',
          credentials: 'same-origin',
          headers: {'Accept': 'application/json'}
        });
        if (!response.ok) throw new Error(`status ${response.status}`);
        const payload = await response.json();
        renderJob(payload.job);
        delay = payload.job?.status === 'running' ? 2000 : 10000;
      } catch (_error) {
        setText('[data-discovery-phase]', 'Phase: status monitor reconnecting');
        delay = 5000;
      }
      window.setTimeout(poll, delay);
    };
    poll();
  }
})();
