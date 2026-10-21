let csrf = '';
let revision = null;
let widgetIdentity = null;
const byId = id => document.getElementById(id);
async function api(path, method = 'GET', data) {
  const response = await fetch('/api/v1' + path, { method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: data === undefined ? undefined : JSON.stringify(data) });
  const body = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(body));
  return body;
}
function show(id, value) { byId(id).textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
async function run(action) { try { show('result', await action()); } catch (err) { show('result', err.message); } }
async function resetWidget() {
  if (window.$chatwoot) window.$chatwoot.reset();
  widgetIdentity = null;
}
byId('login').onsubmit = async event => {
  event.preventDefault();
  await run(async () => { await resetWidget(); const me = await api('/auth/login', 'POST',
    { username: byId('username').value, password: byId('password').value });
    csrf = me.csrf_token; byId('password').value = ''; show('account', { username: me.username, role: me.role }); return '登录成功'; });
};
byId('logout').onclick = () => run(async () => { const result = await api('/auth/logout', 'POST', {});
  await resetWidget(); csrf = ''; show('account', '已退出，旧授权已撤销'); return result; });
function setIdentity() {
  if (widgetIdentity && window.$chatwoot) window.$chatwoot.setUser(widgetIdentity.identifier,
    { identifier_hash: widgetIdentity.identifier_hash, name: '演示客户' });
}
window.addEventListener('chatwoot:ready', setIdentity);
byId('widget').onclick = () => run(async () => {
  widgetIdentity = await api('/chat/identity');
  if (window.$chatwoot) { setIdentity(); window.$chatwoot.toggle('open'); }
  else { const script = document.createElement('script'); script.src = widgetIdentity.base_url + '/packs/js/sdk.js';
    script.onload = () => window.chatwootSDK.run({ websiteToken: widgetIdentity.website_token, baseUrl: widgetIdentity.base_url });
    document.head.appendChild(script); }
  return 'Widget 初始化中；身份只来自当前登录会话';
});
const conversationId = () => { const id = Number(byId('conversation').value); if (!Number.isInteger(id) || id < 1) throw new Error('请输入会话编号'); return id; };
byId('bind').onclick = () => run(() => api('/chat/bind', 'POST', { conversation_id: conversationId() }));
byId('inspect').onclick = () => run(async () => { const value = await api('/staff/conversations/' + conversationId()); revision = value.control_revision; return value; });
async function control(action) {
  if (revision === null) throw new Error('请先读取控制状态');
  const result = await api('/staff/conversations/' + conversationId() + '/' + action, 'POST', { expected_revision: revision });
  revision = null; return result;
}
byId('takeover').onclick = () => run(() => control('takeover'));
byId('return').onclick = () => run(() => control('return-to-bot'));
api('/auth/me').then(me => { csrf = me.csrf_token; show('account', { username: me.username, role: me.role }); }).catch(() => {});
