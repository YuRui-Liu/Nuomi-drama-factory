import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";

const repositoryDocs =
  "https://github.com/YuRui-Liu/Nuomi-drama-factory/blob/main/docs/zh/README.md";

export default withMermaid(
  defineConfig({
    lang: "zh-CN",
    title: "Nuomi Drama Factory Cookbook",
    description: "连接产品功能、漫剧创作方法与技术实现的可维护 Wiki。",
    // Keep internal-link validation strict while allowing the local service URLs
    // intentionally documented in start-software.md.
    ignoreDeadLinks: "localhostLinks",
    cleanUrls: true,
    mermaid: {
      theme: "default",
    },
    themeConfig: {
      nav: [
        { text: "Cookbook 首页", link: "/" },
        { text: "产品功能", link: "/product/01-project-and-novel" },
        { text: "创作方法", link: "/creation/01-creative-philosophy" },
        {
          text: "技术实现",
          items: [
            { text: "系统地图", link: "/system-map" },
            { text: "启动与调试", link: "/start-software" },
            { text: "功能反查", link: "/development/trace-a-feature" },
          ],
        },
        { text: "中文文档", link: repositoryDocs },
      ],
      sidebar: [
        {
          text: "入门",
          items: [
            { text: "Cookbook 首页", link: "/" },
            { text: "启动与本地开发", link: "/start-software" },
            { text: "共享系统地图", link: "/system-map" },
          ],
        },
        {
          text: "产品功能",
          collapsed: false,
          items: [
            { text: "01 项目与小说", link: "/product/01-project-and-novel" },
            { text: "02 拆集与规划", link: "/product/02-episode-planning" },
            { text: "03 角色·场景·道具", link: "/product/03-production-assets" },
            { text: "04 剧本与分镜", link: "/product/04-screenplay-and-storyboard" },
            { text: "05 音频与视频", link: "/product/05-audio-and-video" },
            { text: "06 合成与导出", link: "/product/06-compose-and-export" },
            { text: "07 设置与任务中心", link: "/product/07-settings-and-tasks" },
          ],
        },
        {
          text: "创作方法",
          collapsed: false,
          items: [
            { text: "01 创作哲学", link: "/creation/01-creative-philosophy" },
            { text: "02 端到端创作管线", link: "/creation/02-end-to-end-workflow" },
            { text: "03 拆集与节奏", link: "/creation/03-episode-rhythm" },
            { text: "04 角色一致性", link: "/creation/04-character-consistency" },
            { text: "05 镜头语言", link: "/creation/05-shot-language" },
            { text: "06 视听连续性", link: "/creation/06-audiovisual-continuity" },
            { text: "07 阶段质量门禁", link: "/creation/07-quality-gates" },
            { text: "08 创作复盘模板", link: "/creation/08-retrospective-template" },
          ],
        },
        {
          text: "开发维护",
          items: [
            { text: "功能反查", link: "/development/trace-a-feature" },
            { text: "API 与长任务", link: "/development/add-api-and-task" },
            { text: "存储与项目文件", link: "/development/storage-and-files" },
            { text: "测试策略", link: "/development/testing-strategy" },
          ],
        },
        {
          text: "生产管线",
          items: [
            { text: "01 小说导入", link: "/pipelines/01-ingest" },
            { text: "02 剧集图谱", link: "/pipelines/02-episode-graph" },
            { text: "03 生产资产", link: "/pipelines/03-production-assets" },
            { text: "04 剧本与语义", link: "/pipelines/04-screenplay" },
            { text: "05 分镜与图像", link: "/pipelines/05-storyboard" },
            { text: "06 声音与音频", link: "/pipelines/06-audio" },
            { text: "07 视频生成", link: "/pipelines/07-video" },
            { text: "08 合成与导出", link: "/pipelines/08-compose-export" },
          ],
        },
      ],
      search: {
        provider: "local",
        options: {
          translations: {
            button: {
              buttonText: "搜索文档",
              buttonAriaLabel: "搜索文档",
            },
            modal: {
              noResultsText: "没有找到相关内容",
              resetButtonTitle: "清除查询",
              footer: {
                selectText: "选择",
                navigateText: "切换",
                closeText: "关闭",
              },
            },
          },
        },
      },
      outline: {
        level: [2, 3],
        label: "本页目录",
      },
      docFooter: {
        prev: "上一篇",
        next: "下一篇",
      },
      sidebarMenuLabel: "目录",
      returnToTopLabel: "返回顶部",
      darkModeSwitchLabel: "外观",
      lightModeSwitchTitle: "切换到浅色模式",
      darkModeSwitchTitle: "切换到深色模式",
    },
  }),
);
