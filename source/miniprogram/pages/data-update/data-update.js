const { get, login } = require('../../utils/api');
const dataset = require('../../utils/dataset');

Page({
  data: {
    accessGranted: false,
    password: '',
    verifying: false,
    loading: false,
    importing: false,
    status: {
      detailDate: '',
      summaryDate: '',
      publishDate: '',
      consistencyText: '尚未上傳',
      consistent: false,
      canPublish: false,
      checkedAt: '',
      candidateExists: false,
      validationOk: false,
      validationIssues: [],
      validationWarnings: [],
      entityChanges: {}
    },
    detailFile: '',
    merchantFile: '',
    preview: {}
  },

  _candidate: null,

  onShow() {
    if (this.data.accessGranted) this.refreshPublished();
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },

  async verifyPassword() {
    if (!this.data.password || this.data.verifying) return;
    this.setData({ verifying: true });
    try {
      const me = await get('/api/v1/auth/me', { silent: true });
      const result = await login(me.username, this.data.password);
      if (result?.user?.role !== 'admin') throw new Error('只有管理員可以上傳數據');
      this.setData({ accessGranted: true, password: '' });
      this.refreshPublished();
    } catch (e) {
      wx.showToast({ title: e.message || '驗證失敗', icon: 'none' });
    } finally {
      this.setData({ verifying: false });
    }
  },

  refreshPublished() {
    const meta = dataset.publishedMeta() || {};
    const has = !!(meta.txnCount || meta.merchantCount);
    this.setData({
      status: {
        detailDate: meta.maxDate || meta.asOf || '未上傳',
        summaryDate: meta.asOf || '未上傳',
        publishDate: meta.asOf || '待確認',
        consistencyText: has ? '已載入本地文件' : '尚未上傳數據文件',
        consistent: has,
        canPublish: !!(this._candidate && this._candidate.meta && this._candidate.meta.txnCount),
        checkedAt: meta.importedAt ? String(meta.importedAt).slice(0, 19).replace('T', ' ') : '',
        candidateExists: !!(this._candidate || has),
        validationOk: !!(this._candidate && this._candidate.meta && this._candidate.meta.txnCount),
        validationIssues: [],
        validationWarnings: has && !this._candidate ? ['當前展示的是上次已確認的本地文件'] : [],
        entityChanges: {
          merchant_added: (this._candidate && this._candidate.meta.merchantCount) || meta.merchantCount || 0,
          merchant_removed: 0,
          agent_added: 0,
          agent_removed: 0,
          sales_added: 0,
          sales_removed: 0,
          stale_binding_count: 0
        }
      }
    });
  },

  pickTransactions() {
    this.pickFile('transactions');
  },

  pickMerchants() {
    this.pickFile('merchants');
  },

  pickFile(kind) {
    if (this.data.importing) return;
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['json', 'csv', 'txt'],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        this.readFile(file, kind);
      },
      fail: (err) => {
        if (err.errMsg && err.errMsg.indexOf('cancel') >= 0) return;
        wx.showToast({ title: '請先把文件發到微信，再從聊天記錄選擇', icon: 'none' });
      }
    });
  },

  readFile(file, kind) {
    this.setData({ importing: true });
    wx.getFileSystemManager().readFile({
      filePath: file.path,
      encoding: 'utf8',
      success: (res) => {
        try {
          const parsed = dataset.parseText(res.data, file.name);
          const next = dataset.mergeCandidate(this._candidate, parsed, kind);
          this._candidate = next;
          const name = file.name || '未命名';
          const patch = {
            importing: false,
            preview: next.meta
          };
          if (kind === 'transactions' || parsed.transactions.length) patch.detailFile = name;
          if (kind === 'merchants' || parsed.merchants.length) patch.merchantFile = name;
          if (parsed.transactions.length && parsed.merchants.length) {
            patch.detailFile = name;
            patch.merchantFile = name;
          }
          this.setData(patch);
          this.refreshPublished();
          wx.showToast({
            title: '已讀取 ' + next.meta.merchantCount + ' 商戶 / ' + next.meta.txnCount + ' 筆交易',
            icon: 'none'
          });
        } catch (e) {
          this.setData({ importing: false });
          wx.showToast({ title: e.message || '文件解析失敗', icon: 'none' });
        }
      },
      fail: () => {
        this.setData({ importing: false });
        wx.showToast({ title: '無法讀取文件', icon: 'none' });
      }
    });
  },

  confirmPublish() {
    if (!this._candidate || !this._candidate.meta || !this._candidate.meta.txnCount) {
      wx.showToast({ title: '請先上傳交易明細文件', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '確認更新',
      content: '將用本地文件覆蓋看板數據，截止日期 ' + (this._candidate.meta.asOf || '未知') + '。僅保存在本機，不上傳到服務器。',
      success: (modal) => {
        if (!modal.confirm) return;
        try {
          const meta = dataset.saveSync(this._candidate);
          this._candidate = null;
          this.setData({ preview: meta });
          this.refreshPublished();
          wx.showToast({ title: '已寫入本地數據', icon: 'success' });
        } catch (e) {
          wx.showToast({ title: e.message || '寫入失敗，文件可能過大', icon: 'none' });
        }
      }
    });
  }
});
