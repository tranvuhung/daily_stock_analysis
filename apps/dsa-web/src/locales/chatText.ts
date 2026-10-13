import type { UiLanguage } from '../i18n/uiText';

// Tên/mô tả chiến lược theo ngôn ngữ UI, khoá theo skill id (`strategies/*.yaml` → `name`).
// Id không khớp thì dùng lại tên từ backend.
// Localized strategy names/descriptions keyed by skill id; unknown ids fall back to the backend name.
type SkillText = { name: string; description: string };

export const CHAT_SKILL_TEXT: Record<UiLanguage, Record<string, SkillText>> = {
  vi: {
    bull_trend: { name: 'Xu hướng tăng (mặc định)', description: 'Chiến lược ưu tiên mặc định: nhận diện đường MA xếp tăng, xu hướng tiếp diễn và cơ hội mua khi giá điều chỉnh.' },
    ma_golden_cross: { name: 'Giao cắt vàng MA', description: 'Phát hiện giao cắt vàng của đường MA kèm xác nhận khối lượng — tín hiệu đảo chiều/tiếp diễn xu hướng kinh điển.' },
    volume_breakout: { name: 'Bứt phá kèm khối lượng', description: 'Phát hiện giá vượt kháng cự kèm khối lượng lớn. Phù hợp khi giá tiến sát vùng kháng cự đã biết.' },
    hot_theme: { name: 'Chủ đề nóng', description: 'Theo dõi điểm nóng chính sách, ngành và thị trường; đánh giá sức mạnh chủ đề, mức lan toả ngành và sức mạnh tương đối của cổ phiếu.' },
    shrink_pullback: { name: 'Điều chỉnh khối lượng thấp', description: 'Phát hiện giá điều chỉnh về hỗ trợ MA với khối lượng giảm — điểm vào lý tưởng khi xu hướng tiếp diễn.' },
    event_driven: { name: 'Theo sự kiện', description: 'Đánh giá sức tác động, xác suất thành hiện thực và giới hạn rủi ro của các sự kiện: kết quả kinh doanh, chính sách, M&A, đơn hàng, ra mắt sản phẩm.' },
    box_oscillation: { name: 'Dao động hộp giá', description: 'Nhận diện vùng hộp giá; mua ở đáy hộp, giảm tỷ trọng ở đỉnh hộp. Phù hợp thị trường đi ngang.' },
    growth_quality: { name: 'Chất lượng tăng trưởng', description: 'Kết hợp tăng trưởng doanh thu/lợi nhuận, ROE, dòng tiền và dư địa ngành để nhận diện cổ phiếu tăng trưởng chất lượng và rủi ro chững lại.' },
    bottom_volume: { name: 'Khối lượng tăng tại đáy', description: 'Phát hiện khối lượng tăng mạnh ở vùng đáy sau thời gian dài giảm giá — tín hiệu đảo chiều tiềm năng.' },
    expectation_repricing: { name: 'Định giá lại kỳ vọng', description: 'Phân tích thay đổi kỳ vọng về lợi nhuận, chính sách và định giá; tìm cơ hội khi chênh lệch kỳ vọng được thu hẹp hoặc rủi ro khi kỳ vọng quá nóng.' },
    chan_theory: { name: 'Lý thuyết Chan', description: 'Dựa trên nét, đoạn và trung tâm (zhongshu) của lý thuyết Chan để xác định cấp độ xu hướng, điểm mua/bán và tín hiệu phân kỳ.' },
    wave_theory: { name: 'Lý thuyết sóng Elliott', description: 'Dựa trên cấu trúc sóng đẩy và sóng điều chỉnh Elliott để xác định vị trí sóng hiện tại và giá mục tiêu tiềm năng.' },
    dragon_head: { name: 'Cổ phiếu dẫn dắt', description: 'Nhận diện cổ phiếu dẫn dắt trong luân chuyển ngành. Phù hợp khi ngành bắt đầu tăng hoặc xuất hiện chất xúc tác.' },
    emotion_cycle: { name: 'Chu kỳ tâm lý', description: 'Dựa trên tâm lý thị trường, tỷ lệ quay vòng và cấu trúc giá-khối lượng để nhận diện đáy hoảng loạn và đỉnh hưng phấn, hành động ngược tâm lý.' },
    one_yang_three_yin: { name: 'Một nến tăng kẹp ba nến giảm', description: 'Phát hiện mô hình tích luỹ một nến tăng kẹp ba nến giảm — tín hiệu vào lệnh theo xu hướng tiếp diễn.' },
  },
  en: {
    bull_trend: { name: 'Bullish trend (default)', description: 'Default priority strategy: identifies bullish MA alignment, trend continuation and buy-the-dip opportunities.' },
    ma_golden_cross: { name: 'MA golden cross', description: 'Detects moving-average golden crosses confirmed by volume — a classic trend reversal/continuation signal.' },
    volume_breakout: { name: 'Volume breakout', description: 'Detects breakouts above resistance on heavy volume. Useful when price approaches a known resistance level.' },
    hot_theme: { name: 'Hot theme', description: 'Tracks policy, industry and market hotspots; assesses theme strength, sector spread and relative stock strength.' },
    shrink_pullback: { name: 'Low-volume pullback', description: 'Detects low-volume pullbacks to MA support — an ideal entry during trend continuation.' },
    event_driven: { name: 'Event driven', description: 'Assesses catalyst strength, realization probability and risk bounds for earnings, policy, M&A, orders and product launches.' },
    box_oscillation: { name: 'Box-range oscillation', description: 'Identifies price boxes; buy near the bottom, trim near the top. Suited to sideways markets.' },
    growth_quality: { name: 'Growth quality', description: 'Combines revenue/profit growth, ROE, cash flow and industry room to find quality growth stocks and growth-stall risk.' },
    bottom_volume: { name: 'Bottom volume surge', description: 'Detects volume surges at the bottom after a long decline — a potential trend reversal signal.' },
    expectation_repricing: { name: 'Expectation repricing', description: 'Analyzes shifts in earnings, policy and valuation expectations to find expectation-gap repair or overheating risk.' },
    chan_theory: { name: 'Chan theory', description: 'Uses Chan theory strokes, segments and pivots to judge trend level, buy/sell points and divergence signals.' },
    wave_theory: { name: 'Elliott wave theory', description: 'Uses Elliott impulse and corrective wave structure to locate the current wave and potential price targets.' },
    dragon_head: { name: 'Sector leader', description: 'Identifies leading stocks during sector rotation. Useful when a sector starts moving or a catalyst appears.' },
    emotion_cycle: { name: 'Sentiment cycle', description: 'Uses market sentiment, turnover and price-volume structure to spot panic bottoms and euphoric tops, positioning against sentiment.' },
    one_yang_three_yin: { name: 'One yang, three yin', description: 'Detects the one-bullish-candle-enclosing-three-bearish-candles consolidation pattern — a trend continuation entry signal.' },
  },
};

// Nhãn công cụ trong tiến trình phân tích, khoá theo tên tool backend.
// Progress labels for agent tools, keyed by backend tool name.
export const CHAT_TOOL_LABELS: Record<UiLanguage, Record<string, string>> = {
  vi: {
    get_realtime_quote: 'Lấy giá thời gian thực',
    get_daily_history: 'Lấy dữ liệu nến lịch sử',
    get_analysis_context: 'Lấy ngữ cảnh phân tích',
    get_stock_info: 'Lấy thông tin cơ bản',
    search_stock_news: 'Tìm tin tức cổ phiếu',
    search_comprehensive_intel: 'Tìm thông tin tổng hợp',
    analyze_trend: 'Phân tích xu hướng kỹ thuật',
    calculate_ma: 'Tính hệ thống đường MA',
    get_volume_analysis: 'Phân tích khối lượng',
    analyze_pattern: 'Nhận diện mô hình nến',
    get_market_indices: 'Lấy chỉ số thị trường',
    get_skill_backtest_summary: 'Lấy tổng quan backtest chiến lược',
    get_strategy_backtest_summary: 'Lấy tổng quan backtest chiến lược',
    get_stock_backtest_summary: 'Lấy dữ liệu backtest theo mã',
    get_portfolio_snapshot: 'Lấy ảnh chụp danh mục',
  },
  en: {
    get_realtime_quote: 'Get realtime quote',
    get_daily_history: 'Get historical K-lines',
    get_analysis_context: 'Get analysis context',
    get_stock_info: 'Get stock fundamentals',
    search_stock_news: 'Search stock news',
    search_comprehensive_intel: 'Search comprehensive intelligence',
    analyze_trend: 'Analyze technical trend',
    calculate_ma: 'Compute moving-average system',
    get_volume_analysis: 'Analyze volume changes',
    analyze_pattern: 'Identify candlestick patterns',
    get_market_indices: 'Get market indices',
    get_skill_backtest_summary: 'Get skill backtest overview',
    get_strategy_backtest_summary: 'Get strategy backtest overview',
    get_stock_backtest_summary: 'Get per-stock backtest data',
    get_portfolio_snapshot: 'Get portfolio snapshot',
  },
};

export function localizeChatSkill<T extends { id: string; name: string; description?: string }>(
  skill: T,
  language: UiLanguage,
): T {
  // Dịch theo skill id của chiến lược dựng sẵn; id lạ giữ nguyên tên từ backend.
  // Localize built-in strategies by skill id; unknown ids keep the backend name.
  const text = CHAT_SKILL_TEXT[language][skill.id];
  if (!text) return skill;
  return { ...skill, name: text.name, description: text.description };
}

export function getChatToolLabel(
  tool: string | undefined,
  language: UiLanguage,
  fallback?: string,
): string {
  if (!tool) return fallback || '';
  return CHAT_TOOL_LABELS[language][tool] || fallback || tool;
}

// Tên giai đoạn (stage) của Agent / multi-agent trong tiến trình.
// Agent / multi-agent stage names shown in progress.
const CHAT_STAGE_LABELS: Record<UiLanguage, Record<string, string>> = {
  vi: { agent_loop: 'phân tích Agent', technical: 'phân tích kỹ thuật', intel: 'thu thập thông tin', risk: 'đánh giá rủi ro', decision: 'ra quyết định', portfolio: 'phân tích danh mục', pipeline: 'quy trình' },
  en: { agent_loop: 'agent analysis', technical: 'technical analysis', intel: 'intelligence', risk: 'risk review', decision: 'decision', portfolio: 'portfolio analysis', pipeline: 'pipeline' },
};

export function getChatStageLabel(stage: string | undefined, language: UiLanguage): string {
  const key = stage || 'pipeline';
  return CHAT_STAGE_LABELS[language][key] || key;
}
