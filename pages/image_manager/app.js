const bridge = window.AstrBotPluginPage;

const drop = document.getElementById('drop');
const fileInput = document.getElementById('fileInput');
const imageGrid = document.getElementById('imageGrid');
const tagStats = document.getElementById('tagStats');
const tagCount = document.getElementById('tagCount');
const imageCount = document.getElementById('imageCount');
const refreshBtn = document.getElementById('refreshBtn');
const toast = document.getElementById('toast');

function showToast(msg, isError = false) {
  toast.textContent = msg;
  toast.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => { toast.className = 'toast'; }, 3000);
}

function setStatus(el, text, isError = false) {
  el.innerHTML = `<p class="status${isError ? ' error' : ''}">${text}</p>`;
}

// ==================== 加载图片列表 ====================
async function refreshList() {
  setStatus(imageGrid, '加载中…');
  imageCount.textContent = '';
  try {
    const data = await bridge.apiGet('list');
    const files = data.files || [];
    imageCount.textContent = `（${files.length}）`;
    imageGrid.innerHTML = '';

    if (files.length === 0) {
      setStatus(imageGrid, '暂无图片，请先上传。');
      return;
    }

    files.forEach(item => {
      const card = document.createElement('div');
      card.className = 'image-card';

      // 缩略图
      const imgWrapper = document.createElement('div');
      imgWrapper.className = 'img-wrapper';
      if (item.thumb) {
        const img = document.createElement('img');
        img.src = item.thumb;
        img.alt = item.name;
        img.loading = 'lazy';
        imgWrapper.appendChild(img);
      } else {
        imgWrapper.innerHTML = '<span class="no-thumb">无法预览</span>';
      }

      // 标签
      const tagEl = document.createElement('div');
      tagEl.className = 'tag-label';
      tagEl.textContent = item.tag || '（无标签）';
      tagEl.title = item.tag || '';

      // 文件名（小字）
      const nameEl = document.createElement('div');
      nameEl.className = 'file-name';
      nameEl.textContent = item.name;
      nameEl.title = item.name;

      // 按钮区
      const actions = document.createElement('div');
      actions.className = 'card-actions';

      const editBtn = document.createElement('button');
      editBtn.className = 'edit-btn';
      editBtn.textContent = '改标签';
      editBtn.addEventListener('click', async () => {
        const currentTag = item.tag || '';
        const input = prompt(`修改「${item.name}」的标签：`, currentTag);
        if (input === null) return;
        const newTag = input.trim();
        if (!newTag) { showToast('标签不能为空', true); return; }
        if (newTag === currentTag) return;

        try {
          const res = await bridge.apiPost('update_tag', {
            filename: item.name,
            tag: newTag,
          });
          if (res && res.error) {
            showToast(res.error, true);
            return;
          }
          showToast(`标签已更新为「${newTag}」`);
          await refreshList();
          await refreshTags();
        } catch (e) {
          console.error('[image_tag_kb] 改标签失败:', e);
          showToast('修改失败: ' + (e.message || e), true);
        }
      });

      const delBtn = document.createElement('button');
      delBtn.className = 'del-btn';
      delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => {
        if (!confirm(`确定删除「${item.name}」？`)) return;
        try {
          await bridge.apiPost('delete', { filename: item.name });
          showToast(`已删除 ${item.name}`);
          await refreshList();
          await refreshTags();
        } catch (e) {
          console.error('[image_tag_kb] 删除失败:', e);
          showToast('删除失败: ' + (e.message || e), true);
        }
      });

      actions.appendChild(editBtn);
      actions.appendChild(delBtn);

      card.appendChild(imgWrapper);
      card.appendChild(tagEl);
      card.appendChild(nameEl);
      card.appendChild(actions);
      imageGrid.appendChild(card);
    });
  } catch (e) {
    console.error('[image_tag_kb] refreshList 失败:', e);
    setStatus(imageGrid, '加载图片列表失败：' + (e.message || e), true);
  }
}

// ==================== 加载标签 ====================
async function refreshTags() {
  setStatus(tagStats, '加载中…');
  tagCount.textContent = '';
  try {
    const data = await bridge.apiGet('tags');
    const tags = data.tags || [];
    tagCount.textContent = `（${tags.length}）`;
    tagStats.innerHTML = '';

    if (tags.length === 0) {
      setStatus(tagStats, '暂无标签，请先上传图片。');
      return;
    }

    tagStats.innerHTML = tags
      .map(t => `<span class="tag-chip" title="${t.filename}">${t.tag}</span>`)
      .join('');
  } catch (e) {
    console.error('[image_tag_kb] refreshTags 失败:', e);
    setStatus(tagStats, '加载标签失败：' + (e.message || e), true);
  }
}

// ==================== 上传逻辑 ====================
function bindUpload() {
  drop.addEventListener('click', () => fileInput.click());

  drop.addEventListener('dragover', e => {
    e.preventDefault();
    drop.classList.add('over');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', async e => {
    e.preventDefault();
    drop.classList.remove('over');
    await uploadFiles(e.dataTransfer.files);
  });

  fileInput.addEventListener('change', async e => {
    await uploadFiles(e.target.files);
  });
}

async function uploadFiles(files) {
  const images = Array.from(files).filter(f => f.type.startsWith('image/'));
  if (images.length === 0) {
    showToast('没有检测到图片文件', true);
    return;
  }

  const total = images.length;
  let ok = 0, fail = 0;
  const textEl = drop.querySelector('p');

  for (const file of images) {
    try {
      await bridge.upload('upload', file);
      ok++;
      textEl.textContent = `上传中… ${ok + fail}/${total}`;
    } catch (e) {
      console.error('[image_tag_kb] 上传失败:', file.name, e);
      fail++;
    }
  }

  fileInput.value = '';
  textEl.textContent = '点击或拖拽图片到此处上传';

  if (fail === 0) {
    showToast(`上传完成 ${ok}/${total}`);
  } else if (ok === 0) {
    showToast(`全部上传失败 (${fail})`, true);
  } else {
    showToast(`成功 ${ok}，失败 ${fail}`, true);
  }

  await refreshList();
  await refreshTags();
}

// ==================== 初始化 ====================
(async () => {
  try {
    await bridge.ready();
  } catch (e) {
    console.error('[image_tag_kb] bridge.ready 失败:', e);
    setStatus(imageGrid, '页面初始化失败：' + (e.message || e), true);
    setStatus(tagStats, '页面初始化失败', true);
    return;
  }

  bindUpload();
  refreshBtn.addEventListener('click', () => {
    refreshList();
    refreshTags();
  });

  await refreshList();
  await refreshTags();
})();