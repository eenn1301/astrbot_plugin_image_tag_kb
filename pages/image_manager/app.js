const bridge = window.AstrBotPluginPage;
const drop = document.getElementById('drop');
const fileInput = document.getElementById('fileInput');
const imageGrid = document.getElementById('imageGrid');
const tagStats = document.getElementById('tagStats');

await bridge.ready();

// ==================== 加载图片列表 ====================
async function refreshList() {
  const data = await bridge.apiGet('list');
  const files = data.files || [];

  imageGrid.innerHTML = '';
  if (files.length === 0) {
    imageGrid.innerHTML = '<p class="empty">暂无图片，请先上传。</p>';
    return;
  }

  files.forEach(item => {
    const card = document.createElement('div');
    card.className = 'image-card';

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

    const nameEl = document.createElement('div');
    nameEl.className = 'file-name';
    nameEl.textContent = item.name;
    nameEl.title = item.name;

    const delBtn = document.createElement('button');
    delBtn.className = 'del-btn';
    delBtn.textContent = '删除';
    delBtn.addEventListener('click', async () => {
      if (!confirm(`确定删除「${item.name}」？`)) return;
      await bridge.apiPost('delete', { filename: item.name });
      await refreshList();
      await refreshTags();
    });

    card.appendChild(imgWrapper);
    card.appendChild(nameEl);
    card.appendChild(delBtn);
    imageGrid.appendChild(card);
  });
}

// ==================== 加载标签统计 ====================
async function refreshTags() {
  try {
    const data = await bridge.apiGet('tags');
    const tags = data.tags || [];
    if (tags.length === 0) {
      tagStats.innerHTML = '';
      return;
    }
    tagStats.innerHTML = tags
      .map(t => `<span class="tag-chip">${t.tag} <em>(${t.count})</em></span>`)
      .join('');
  } catch (e) {
    // tags 接口不存在时静默忽略
  }
}

// ==================== 上传逻辑 ====================
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

async function uploadFiles(files) {
  const total = files.length;
  let done = 0;
  for (const file of files) {
    if (!file.type.startsWith('image/')) continue;
    await bridge.upload('upload', file);
    done++;
    drop.querySelector('p').textContent = `上传中... ${done}/${total}`;
  }
  fileInput.value = '';
  drop.querySelector('p').textContent = '点击或拖拽图片到此处上传';
  await refreshList();
  await refreshTags();
}

// ==================== 初始化 ====================
refreshList();
refreshTags();