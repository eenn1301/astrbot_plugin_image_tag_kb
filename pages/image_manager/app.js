const bridge = window.AstrBotPluginPage;
const drop = document.getElementById('drop');
const fileInput = document.getElementById('fileInput');
const fileList = document.getElementById('fileList');

await bridge.ready();

async function refreshList() {
  const data = await bridge.apiGet('list');
  fileList.innerHTML = '';
  (data.files || []).forEach(name => {
    const li = document.createElement('li');
    li.innerHTML = `<span>${name}</span>
      <button data-name="${name}" class="del">删除</button>`;
    fileList.appendChild(li);
  });
  fileList.querySelectorAll('.del').forEach(btn => {
    btn.addEventListener('click', async () => {
      await bridge.apiPost('delete', { filename: btn.dataset.name });
      refreshList();
    });
  });
}

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
  for (const file of files) {
    if (!file.type.startsWith('image/')) continue;
    await bridge.upload('upload', file);
  }
  fileInput.value = '';
  await refreshList();
}

refreshList();