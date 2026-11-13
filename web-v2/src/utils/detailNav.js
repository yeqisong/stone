/** 记录进入详情页前的列表顺序，供详情页「上一只/下一只」使用。 */
export function rememberDetailNavList(codes) {
  try {
    const list = (codes || []).filter(Boolean).map(String)
    localStorage.setItem('detail_nav_list', JSON.stringify(list))
  } catch (e) { /* localStorage 不可用时静默 */ }
}
