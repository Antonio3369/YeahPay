const { get, post, put } = require('../../utils/api');
const { getRole } = require('../../utils/auth');

const ROLE_LABELS = ['管理員', '代理商', '銷售'];
const ROLE_VALUES = ['admin', 'supervisor', 'sales'];

Page({
  data: {
    role: '',
    users: [],
    agents: [],
    sales: [],
    salesOptions: [],
    loading: false,
    editorOpen: false,
    editingId: null,
    editingUser: null,
    roleLabels: ROLE_LABELS,
    form: {
      username: '',
      password: '',
      display_name: '',
      role: 'sales',
      roleIndex: 2,
      scopeType: 'sales',
      scopeValue: '',
      scopeIndex: 0,
      scopeLabels: [],
      agentId: '',
      agentIndex: 0,
      agentLabels: [],
      salesNames: [],
      salesValue: '',
      salesIndex: 0,
      email: '',
      phone: ''
    }
  },

  onLoad() {
    const role = getRole();
    if (role !== 'admin') {
      wx.showToast({ title: '只有管理員可以進入', icon: 'none' });
      setTimeout(() => wx.navigateBack(), 500);
      return;
    }
    this.setData({ role });
  },

  onShow() {
    // 账号页可能长期保留在页面栈中；数据库发布后重新进入时刷新可绑定的
    // 代理商和销售，但不打断正在编辑的表单。
    if (this.data.role === 'admin' && !this.data.editorOpen) this.loadData();
  },

  goBack() {
    wx.navigateBack();
  },

  async loadData() {
    this.setData({ loading: true });
    try {
      const [users, agents, sales] = await Promise.all([
        get('/api/v1/admin/users', { silent: true }),
        get('/api/v1/scope/agents', { silent: true }),
        get('/api/v1/scope/sales', { silent: true })
      ]);
      this.setData({
        users: (users || []).map(u => ({
          ...u,
          roleLabel: ROLE_LABELS[ROLE_VALUES.indexOf(u.role)] || u.role,
          statusLabel: u.is_active ? '啟用中' : '已停用',
          scopeLabel: u.scope_label || (u.scope_type === 'all' ? '全部機構' : (u.scope_value || '未綁定範圍'))
        })),
        agents: agents || [],
        sales: sales || [],
        salesOptions: (sales || []).map(s => ({ ...s, selected: false }))
      });
    } catch (e) {
      wx.showToast({ title: e.message || '賬號列表讀取失敗', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  openCreate() {
    const form = this.makeForm({ role: 'sales' });
    this.setData({
      editorOpen: true,
      editingId: null,
      editingUser: null,
      form,
      salesOptions: this.buildSalesOptions(form.salesNames)
    });
  },

  openEdit(e) {
    const user = e.currentTarget.dataset.user;
    if (!user) return;
    const form = this.makeForm(user);
    this.setData({
      editorOpen: true,
      editingId: user.id,
      editingUser: user,
      form,
      salesOptions: this.buildSalesOptions(form.salesNames)
    });
  },

  makeForm(source = {}) {
    const role = source.role || 'sales';
    const roleIndex = Math.max(0, ROLE_VALUES.indexOf(role));
    const scopeType = source.scope_type || (role === 'admin' ? 'all' : role === 'supervisor' ? 'agent' : 'sales');
    const options = this.getScopeOptions(scopeType);
    const scopeValue = source.scope_value || (scopeType === 'all' ? '' : (options[0]?.value || ''));
    const scopeIndex = Math.max(0, options.findIndex(o => String(o.value) === String(scopeValue)));
    const agentOptions = [{ value: '', label: '不綁定代理商' }, ...this.data.agents.map(a => ({ value: String(a.agent_id), label: a.agent_name || a.agent_id }))];
    const agentId = source.agent_id ? String(source.agent_id) : '';
    const agentIndex = Math.max(0, agentOptions.findIndex(o => String(o.value) === agentId));
    let salesNames = Array.isArray(source.sales_names) && source.sales_names.length
      ? source.sales_names.map(String)
      : (role === 'sales' && source.scope_value ? [String(source.scope_value)] : []);
    if (role === 'sales' && !salesNames.length && this.data.sales.length) {
      salesNames = [String(this.data.sales[0].sales_name)];
    }
    const salesIndex = Math.max(0, this.data.sales.findIndex(s => String(s.sales_name) === String(salesNames[0] || '')));
    return {
      username: source.username || '',
      password: '',
      display_name: source.display_name || '',
      role,
      roleIndex,
      scopeType,
      scopeValue,
      scopeIndex,
      scopeLabels: options.map(o => o.label),
      agentId,
      agentIndex,
      agentLabels: agentOptions.map(o => o.label),
      salesNames,
      salesValue: salesNames[0] || '',
      salesIndex,
      email: source.email || '',
      phone: source.phone || ''
    };
  },

  getScopeOptions(scopeType) {
    if (scopeType === 'agent') {
      return this.data.agents.map(a => ({ value: a.agent_id, label: a.agent_name || a.agent_id }));
    }
    if (scopeType === 'sales') {
      return this.data.sales.map(s => ({ value: s.sales_name, label: s.sales_name }));
    }
    return [];
  },

  buildSalesOptions(selected = []) {
    const selectedSet = new Set((selected || []).map(String));
    return this.data.sales.map(s => ({ ...s, selected: selectedSet.has(String(s.sales_name)) }));
  },

  onRoleChange(e) {
    const role = ROLE_VALUES[Number(e.detail.value)] || 'sales';
    const scopeType = role === 'admin' ? 'all' : role === 'supervisor' ? 'agent' : 'sales';
    const options = this.getScopeOptions(scopeType);
    this.setData({
      'form.role': role,
      'form.roleIndex': Number(e.detail.value),
      'form.scopeType': scopeType,
      'form.scopeValue': options[0]?.value || '',
      'form.scopeIndex': 0,
      'form.scopeLabels': options.map(o => o.label)
      , 'form.agentId': ''
      , 'form.agentIndex': 0
      , 'form.salesNames': []
      , 'form.salesValue': ''
      , 'form.salesIndex': 0
      , salesOptions: this.buildSalesOptions([])
    });
  },

  onAgentChange(e) {
    const index = Number(e.detail.value);
    const options = [{ value: '', label: '不綁定代理商' }, ...this.data.agents.map(a => ({ value: String(a.agent_id), label: a.agent_name || a.agent_id }))];
    this.setData({ 'form.agentIndex': index, 'form.agentId': options[index]?.value || '' });
  },

  onSalesChange(e) {
    const values = (e.detail.value || []).map(String);
    this.setData({ 'form.salesNames': values, salesOptions: this.buildSalesOptions(values) });
  },

  onSalesPickerChange(e) {
    const index = Number(e.detail.value);
    const value = this.data.sales[index]?.sales_name || '';
    this.setData({ 'form.salesIndex': index, 'form.salesValue': value, 'form.salesNames': value ? [value] : [], salesOptions: this.buildSalesOptions(value ? [value] : []) });
  },

  onScopeChange(e) {
    const index = Number(e.detail.value);
    const options = this.getScopeOptions(this.data.form.scopeType);
    this.setData({
      'form.scopeIndex': index,
      'form.scopeValue': options[index]?.value || ''
    });
  },

  onInput(e) {
    this.setData({ ['form.' + e.currentTarget.dataset.field]: e.detail.value });
  },

  closeEditor() {
    this.setData({ editorOpen: false });
  },

  stopEvent() {},

  async saveUser() {
    const f = this.data.form;
    if (!f.display_name.trim()) return wx.showToast({ title: '請輸入顯示名稱', icon: 'none' });
    if (!this.data.editingId && (!f.username.trim() || !f.password)) {
      return wx.showToast({ title: '請填寫賬號和初始密碼', icon: 'none' });
    }
    if (f.password && f.password.length < 6) {
      return wx.showToast({ title: '密碼至少需要 6 位', icon: 'none' });
    }
    if (f.role === 'sales' && !f.salesValue) {
      return wx.showToast({ title: '請選擇銷售範圍', icon: 'none' });
    }
    if (f.role === 'supervisor' && !f.agentId && !f.salesNames.length) {
      return wx.showToast({ title: '請至少選擇一個代理商或銷售', icon: 'none' });
    }
    const payload = {
      display_name: f.display_name.trim(),
      role: f.role,
      scope_type: f.scopeType,
      scope_value: f.scopeType === 'all' ? null : f.scopeValue,
      agent_id: f.role === 'supervisor' ? (f.agentId || null) : null,
      sales_names: f.role === 'admin' ? [] : (f.role === 'sales' ? [f.salesValue] : f.salesNames),
      email: f.email.trim() || null,
      phone: f.phone.trim() || null
    };
    try {
      let userId = this.data.editingId;
      if (userId) {
        await put('/api/v1/admin/users/' + userId, payload);
      } else {
        const result = await post('/api/v1/admin/users', { username: f.username.trim(), password: f.password, ...payload });
        userId = result.id;
      }
      // 新建时 POST /admin/users 已经保存了初始密码；只有编辑已有账号时才调用重置密码接口。
      if (f.password && this.data.editingId) {
        await put('/api/v1/v2/admin/users/' + userId + '/password', { new_password: f.password });
      }
      wx.showToast({ title: this.data.editingId ? '已儲存' : '賬號已建立', icon: 'success' });
      this.setData({ editorOpen: false, editingUser: null });
      await this.loadData();
    } catch (e) {
      wx.showToast({ title: e.message || '儲存失敗', icon: 'none' });
    }
  },

  async toggleActive(e) {
    const user = e.currentTarget.dataset.user;
    if (!user || user.id === 1 && user.is_active) {
      wx.showToast({ title: '不能停用當前超級管理員', icon: 'none' });
      return;
    }
    const nextActive = !user.is_active;
    const modal = await new Promise(resolve => wx.showModal({
      title: nextActive ? '啟用賬號' : '停用賬號',
      content: (nextActive ? '確定啟用 ' : '確定停用 ') + user.username + '？',
      success: resolve
    }));
    if (!modal.confirm) return;
    try {
      await put('/api/v1/admin/users/' + user.id, { is_active: nextActive });
      await this.loadData();
    } catch (e) {
      wx.showToast({ title: e.message || '操作失敗', icon: 'none' });
    }
  }
});
