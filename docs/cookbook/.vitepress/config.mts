import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";

const repositoryDocs =
  "https://github.com/YuRui-Liu/Nuomi-drama-factory/blob/main/docs/zh/README.md";

export default withMermaid(
  defineConfig({
    lang: "zh-CN",
    title: "Nuomi Drama Factory 开发者 Cookbook",
    description: "按修改目标定位原理、调用管线、关键代码和验证方式。",
    ignoreDeadLinks: false,
    cleanUrls: true,
    mermaid: {
      theme: "default",
    },
    themeConfig: {
      nav: [
        { text: "Cookbook 首页", link: "/" },
        { text: "系统地图", link: "/system-map" },
        { text: "启动与调试", link: "/start-software" },
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
