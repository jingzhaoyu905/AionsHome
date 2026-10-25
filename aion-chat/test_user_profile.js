'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('profile groups sort by update time and collapse ended entries without changing edit indices', () => {
  const elements = { entries: { innerHTML: '' } };
  const context = vm.createContext({ document: { getElementById: id => elements[id] } });
  const source = fs.readFileSync(path.join(__dirname, 'static/user-profile.js'), 'utf8');
  vm.runInContext(source.slice(0, source.indexOf("profileEl('entries').addEventListener")), context);
  vm.runInContext(`
    profileData = {categories:{recent:'近期生活与待办'},
      subcategories:{recent:{plans:'待办与安排',preferences:'近期偏好',ideas:'想做的事'}},
      statuses:{active:'当前关注',done:'已完成'}};
    draftEntries = [
      {key:'old',category:'recent',subcategory:'preferences',content:'旧偏好',updated_at:10},
      {key:'done',category:'recent',content:'结束事项',view_status:'done',updated_at:40},
      {key:'new',category:'recent',subcategory:'preferences',content:'新偏好',updated_at:20},
      {key:'pending',category:'recent',content:'未完成事项',next_step:'明早取药',view_status:'active',updated_at:30},
      {key:'person',category:'recent',subcategory:'ideas',content:'个人背景',updated_at:10},
      {key:'fact',category:'recent',subcategory:'ideas',content:'鱼缸养黄金螺',updated_at:20,omission_reason:'按话题选用'}
    ];
    renderProfileEntries();
  `, context);
  const html = elements.entries.innerHTML;
  assert.ok(html.includes('下一步：明早取药'));
  assert.ok(html.indexOf('待办与安排') < html.indexOf('近期偏好')); 
  assert.ok(html.indexOf('新偏好') < html.indexOf('旧偏好'));
  assert.match(html, /data-index="2"[^>]*>[\s\S]*?新偏好/);
  assert.match(html, /<section class="profile-current"[\s\S]*?当前带入 AI[\s\S]*?未完成事项[\s\S]*?<\/section>\s*<details/);
  assert.match(html, /<details[^>]*>[\s\S]*?已结束与归档[\s\S]*?1 项[\s\S]*?不再带入 AI[\s\S]*?<\/summary>[\s\S]*?结束事项/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.ok(html.indexOf('未完成事项') < html.indexOf('<details'));
});
