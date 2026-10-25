(function (root) {
  'use strict';
  const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll("'", '&#39;');

  function attributes(url, escaped = false) {
    let original = String(url || '');
    if (escaped) {
      const text = document.createElement('textarea');
      text.innerHTML = original;
      original = text.value;
    }
    let source = original;
    try {
      const parsed = new URL(original, root.location.href);
      if (parsed.origin === root.location.origin
          && /^\/(uploads|cr-uploads|public|screenshots)\//.test(parsed.pathname)
          && !parsed.pathname.startsWith('/public/wallpaper/')
          && /\.(jpe?g|png|webp)$/i.test(parsed.pathname)) {
        source = '/api/chat-media/thumbnail?src=' + encodeURIComponent(parsed.pathname);
      }
    } catch (_) {}
    // Reserve space before lazy images load so off-screen history stays off-screen.
    return `src="${escape(source)}" data-original-src="${escape(original)}" loading="lazy" decoding="async" width="120" height="90" style="object-fit:contain;max-width:100%;border-radius:8px;cursor:pointer" onerror="this.onerror=null;this.src=this.dataset.originalSrc"`;
  }

  function original(img) { return img.dataset?.originalSrc || img.src; }
  function group(images) {
    if (images.length < 2) return images.join('');
    return `<div class="chat-image-group" data-image-gallery><button type="button" class="chat-image-toggle" aria-expanded="false" onclick="ChatImagePreview.toggle(this)">展开 ${images.length}</button><div class="chat-image-cards">${images.join('')}</div></div>`;
  }

  function toggle(button) {
    const group = button.closest('.chat-image-group');
    const top = group.getBoundingClientRect().top;
    const expanded = group.classList.toggle('expanded');
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = `${expanded ? '收起' : '展开'} ${group.querySelectorAll('img').length}`;
    // Keep the control under the finger if scroll anchoring moves the message.
    let scroll = group.parentElement;
    while (scroll && !/auto|scroll/.test(getComputedStyle(scroll).overflowY)) scroll = scroll.parentElement;
    if (scroll) scroll.scrollTop += group.getBoundingClientRect().top - top;
  }
  root.ChatImagePreview = { attributes, original, group, toggle };
})(typeof window !== 'undefined' ? window : globalThis);
