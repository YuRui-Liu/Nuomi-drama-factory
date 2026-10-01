export interface DirectorConfig extends Record<string, unknown> {
  pace: string; camera_motion: string; composition: string; performance: string; method: string; source_text: string; allow_adaptation: boolean;
}
export const emptyDirectorConfig: DirectorConfig = { pace: "", camera_motion: "", composition: "", performance: "", method: "", source_text: "", allow_adaptation: false };
export const directorPresets = [
  { name: "TVC 广告", code: "TVC", description: "突出主体、质感与记忆点", tags: ["产品特写", "清晰节奏"], data: { ...emptyDirectorConfig, pace: "开场建立记忆点，中段展示核心信息，结尾留出品牌识别时间", camera_motion: "平稳推进与产品细节特写，转场保持运动方向连续", composition: "主体突出，控制背景层次，为现有字幕与品牌标识留出空间", performance: "自然、明确，用可见动作传达使用体验", method: "围绕原剧本已有卖点组织镜头，不新增未经证实的产品效果或承诺。" } },
  { name: "悬疑叙事", code: "THRILLER", description: "用信息差牵引下一镜", tags: ["负空间", "线索递进"], data: { ...emptyDirectorConfig, pace: "线索逐层揭示，关键发现前保留短暂停顿", camera_motion: "有动机的缓推与视线跟随，揭示线索时切入细节", composition: "用遮挡、画外空间与人物视线形成信息差", performance: "克制的目光与呼吸变化，避免提前泄露人物意图", method: "只调整原有线索的视觉呈现，不新增谜底、人物动机或故事事实。" } },
  { name: "口播带货", code: "TALK", description: "人物可信，信息一眼清楚", tags: ["稳定中景", "演示细节"], data: { ...emptyDirectorConfig, pace: "跟随原有口播信息分段，每个要点留出理解时间", camera_motion: "以稳定中近景为主，已有演示动作处切换细节机位", composition: "保持人物视线明确，商品与手部演示完整入画，预留字幕区", performance: "语气自然、动作简洁，不夸大情绪与使用反应", method: "保留原对白与产品事实；镜头支持信息表达，不替文案添加销量、疗效或价格承诺。" } },
  { name: "动画剧情", code: "ANIMATION", description: "动作轮廓清晰，情绪读得懂", tags: ["明确剪影", "动作衔接"], data: { ...emptyDirectorConfig, pace: "建立动作预备、主体动作与反应的清晰节拍", camera_motion: "跟随角色主要动作，重要反应使用稳定机位", composition: "优先保证角色剪影、空间关系和动作方向可读", performance: "适度强化姿态与表情，保持角色性格和原有动机", method: "按原情节组织动作连续性与视线衔接，不擅自增加剧情、对白或角色设定。" } },
  { name: "纪录片", code: "DOCUMENTARY", description: "观察环境，保留真实反应", tags: ["环境关系", "自然表演"], data: { ...emptyDirectorConfig, pace: "随事件自然展开，保留必要的环境与反应时长", camera_motion: "以观察式固定机位和轻微跟拍呈现现场关系", composition: "人物与环境共同叙事，避免过度摆拍式对称", performance: "自然、不过度表演，保留停顿与即时反应", method: "忠于现有素材与事实，不将推测拍成已发生的事实，不制造采访引语。" } },
  { name: "卡点 MV", code: "MUSIC", description: "围绕节拍安排视觉变化", tags: ["动作匹配", "节奏切换"], data: { ...emptyDirectorConfig, pace: "在已有音乐节拍信息明确时组织镜头长短与视觉重音", camera_motion: "以短促推拉、方向一致的运动与动作匹配形成节奏", composition: "建立重复的视觉母题，在景别和主体位置上形成变化", performance: "动作节奏明确，重点姿态与视觉重音对应", method: "仅提供镜头节奏偏好；没有音频节拍分析时不声称自动精准卡点，保留原剧情和对白。" } },
  { name: "克制观察", code: "OBSERVE", description: "把空间留给细微反应", tags: ["留白", "人物关系"], data: { ...emptyDirectorConfig, pace: "保留停顿，留出反应时间", camera_motion: "固定机位，避免无动机运动", composition: "以人物关系组织构图，保留视线空间", performance: "克制、自然，强调细微反应", method: "用视线与空间关系服务原情节，不改写人物动机和对白。" } },
  { name: "悬念推进", code: "REVEAL", description: "缓慢靠近，等待转折", tags: ["缓推", "紧张感"], data: { ...emptyDirectorConfig, pace: "逐步释放信息", camera_motion: "缓慢推进，转折处保持稳定", composition: "使用前景遮挡与负空间强化未知", performance: "收敛动作，以目光和呼吸体现压力", method: "通过镜头控制信息揭示，不新增原稿未包含的线索或故事事实。" } },
];
export function directorSuggestions(shots: Array<{ id: string; camera_motion: string; composition: string }>, config: Partial<DirectorConfig>) {
  return shots.flatMap((shot) => {
    const changes: { camera_motion?: string; composition?: string } = {};
    for (const field of ["camera_motion", "composition"] as const) {
      const value = config[field]?.trim();
      if (value && value !== shot[field]) changes[field] = value;
    }
    return Object.keys(changes).length ? [{ shot_id: shot.id, changes }] : [];
  });
}
