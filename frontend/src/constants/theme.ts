import { theme, type ThemeConfig } from 'antd'

/**
 * antd v5 主题 token（light/dark 两套），视觉对齐 新ui.html / base.css。
 *
 * 浅色为默认主题：Ant Design 蓝 #1677ff 主色、#f0f2f5 页面底、白卡片。
 * 深色变体采用 AntD dark 算法 + 同品牌蓝。
 * CSS 侧始终以 base.css 的 token（var(--accent) 等）为准。
 */
export type Theme = 'dark' | 'light'

const SHAPE = {
  borderRadius: 8,
  controlHeight: 36,
  fontSize: 14,
} as const

export const ANTD_THEME: Record<Theme, ThemeConfig> = {
  light: {
    algorithm: theme.defaultAlgorithm,
    token: {
      colorPrimary: '#1677ff',
      colorInfo: '#1677ff',
      colorSuccess: '#52c41a',
      colorWarning: '#fa8c16',
      colorError: '#f5222d',
      colorLink: '#1677ff',
      colorBgBase: '#f0f2f5', // == --bg（新ui.html 页面底）
      colorBgContainer: '#ffffff', // == --surface（白卡）
      colorBgElevated: '#ffffff',
      colorBorder: '#d9d9d9', // == --border-strong
      colorBorderSecondary: '#f0f0f0', // == --border
      colorText: 'rgba(0,0,0,0.88)', // ≈ --fg #333
      colorTextSecondary: '#666666', // == --muted
      colorTextTertiary: '#999999', // == --faint
      colorTextQuaternary: '#bfbfbf',
      colorSplit: '#f0f0f0',
      ...SHAPE,
    },
    components: {
      Table: {
        headerBg: '#fafafa',
        headerColor: '#666666',
        rowHoverBg: 'rgba(22,119,255,0.04)',
        borderColor: '#f0f0f0',
        headerSplitColor: 'transparent',
      },
      Menu: {
        itemBorderRadius: 6,
        itemSelectedBg: '#e6f4ff',
        itemSelectedColor: '#1677ff',
        itemColor: '#666666',
        itemHoverColor: '#333333',
      },
      Button: {
        defaultBg: '#ffffff',
        defaultBorderColor: '#d9d9d9',
        primaryShadow: '0 2px 0 rgba(5,145,255,0.1)',
      },
      Modal: {
        contentBg: '#ffffff',
        headerBg: '#ffffff',
        titleFontSize: 16,
      },
      Alert: {
        colorInfoBg: '#e6f4ff',
        colorInfoBorder: '#91caff',
      },
      Collapse: {
        headerBg: 'transparent',
        contentBg: 'transparent',
      },
      Tooltip: {
        colorBgSpotlight: 'rgba(0,0,0,0.85)',
      },
      Tabs: {
        horizontalItemGutter: 8,
      },
    },
  },

  dark: {
    algorithm: theme.darkAlgorithm,
    token: {
      colorPrimary: '#177ddc',
      colorInfo: '#177ddc',
      colorSuccess: '#49aa19',
      colorWarning: '#d89614',
      colorError: '#dc4446',
      colorLink: '#177ddc',
      colorBgBase: '#14161a', // ≈ --bg(dark)
      colorBgContainer: 'rgba(255,255,255,0.045)', // == --surface(dark)
      colorBgElevated: '#1f2329', // 浮层（Modal/Dropdown）不透明底
      colorBorder: 'rgba(255,255,255,0.24)', // == --border-strong(dark)
      colorBorderSecondary: 'rgba(255,255,255,0.10)', // == --border(dark)
      colorText: '#e8ecf4', // == --fg(dark)
      colorTextSecondary: '#a7b0c0', // ≈ --muted(dark)
      colorTextTertiary: '#7d8697', // ≈ --faint(dark)
      colorTextQuaternary: '#5c6575',
      colorSplit: 'rgba(255,255,255,0.10)',
      ...SHAPE,
    },
    components: {
      Table: {
        headerBg: 'rgba(255,255,255,0.04)',
        headerColor: '#a7b0c0',
        rowHoverBg: 'rgba(23,125,220,0.10)',
        borderColor: 'rgba(255,255,255,0.08)',
        headerSplitColor: 'transparent',
      },
      Menu: {
        itemBorderRadius: 6,
        itemSelectedBg: 'rgba(23,125,220,0.18)',
        itemSelectedColor: '#3c9ae8',
        itemColor: '#a7b0c0',
        itemHoverColor: '#e8ecf4',
      },
      Button: {
        defaultBg: 'rgba(255,255,255,0.06)',
        defaultBorderColor: 'rgba(255,255,255,0.20)',
      },
      Modal: {
        contentBg: '#1f2329',
        headerBg: '#1f2329',
        titleFontSize: 16,
      },
      Alert: {
        colorInfoBg: 'rgba(23,125,220,0.12)',
        colorInfoBorder: 'rgba(23,125,220,0.35)',
      },
      Collapse: {
        headerBg: 'transparent',
        contentBg: 'transparent',
      },
      Tooltip: {
        colorBgSpotlight: '#1f2329',
      },
      Tabs: {
        horizontalItemGutter: 8,
      },
    },
  },
}
