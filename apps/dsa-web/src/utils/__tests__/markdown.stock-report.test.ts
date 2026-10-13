import { describe, expect, it } from 'vitest';
import { markdownToPlainText } from '../markdown';

/**
 * Stock report specific tests for markdownToPlainText
 * Tests real-world stock analysis report scenarios
 */
describe('markdownToPlainText - Stock Report Scenarios', () => {
  it('handles typical Vietnamese stock report with tables and indicators', () => {
    const stockReport = `# Hoa Phat (HPG) báo cáo phân tích

## Phân tích kỹ thuật

| Chỉ báo | Giá trị hiện tại | Tín hiệu |
|------|--------|------|
| MA5 | 1680.50 | 🟢 |
| MA10 | 1675.30 | 🟢 |
| MA20 | 1665.80 | 🟢 |

**MACD**: Tín hiệu giao cắt vàng, tham khảo mua
**RSI**: 56.8, ở vùng trung tính

## Phân tích cơ bản

- **P/E**: 28.5
- **P/B**: 8.2
- **Tăng trưởng doanh thu**: +15.3% YoY

> Cảnh báo rủi ro: biến động ngắn hạn tăng, nên kiểm soát tỷ trọng

## Khuyến nghị giao dịch

\`\`\`python
# Vùng mua khuyến nghị
entry_zone = [1650, 1680]
stop_loss = 1620
target = 1750
\`\`\`

[Xem dữ liệu chi tiết](https://example.com/stock/HPG)`;

    const result = markdownToPlainText(stockReport);

    // Verify key content is preserved
    expect(result).toContain('Hoa Phat');
    expect(result).toContain('HPG');
    expect(result).toContain('Phân tích kỹ thuật');
    expect(result).toContain('MACD');
    expect(result).toContain('Tín hiệu giao cắt vàng');
    expect(result).toContain('P/E');
    expect(result).toContain('Cảnh báo rủi ro');
    expect(result).toContain('entry_zone');
    expect(result).toContain('Xem dữ liệu chi tiết');

    // Verify markdown symbols are removed
    expect(result).not.toMatch(/^#{1,6}\s+/m);
    expect(result).not.toMatch(/\*\*[^*]+\*\*/);
    // Note: remove-markdown preserves table structure with pipe characters
    // This is a known limitation - tables remain pipe-separated
  });

  it('handles FPT stock report with English and Vietnamese mix', () => {
    const hkReport = `# FPT Corp (FPT) Technical Analysis

## Key Indicators

* **Current Price**: VND 368.20
* **Change**: +2.5% 📈
* **Volume**: 18.2M

## Support & Resistance

1. **Resistance 1**: VND 375.00
2. **Resistance 2**: VND 380.00
3. **Support 1**: VND 365.00

> Theo dõi khi giá điều chỉnh về vùng 365-368

\`\`\`
MA5 > MA10 > MA20 (xếp tăng)
RSI(14) = 58.3 (trung tính nghiêng tăng)
\`\`\`

[Click for more details](https://example.com/stock/FPT)`;

    const result = markdownToPlainText(hkReport);

    expect(result).toContain('FPT Corp');
    expect(result).toContain('FPT');
    expect(result).toContain('368.20');
    expect(result).toContain('Resistance 1');
    expect(result).toContain('Support 1');
    expect(result).toContain('Theo dõi khi giá điều chỉnh');
    expect(result).toContain('MA5 > MA10');
    expect(result).toContain('Click for more details');
  });

  it('handles English stock report with financial data', () => {
    const usReport = `# Vietcombank JSC (FPT) Analysis Report

## Financial Metrics

| Metric | Value | Change |
|--------|-------|--------|
| Price | $178.35 | +1.2% |
| Market Cap | $2.8T | - |
| P/E Ratio | 28.5 | - |
| EPS | $6.16 | +8.3% |

## Technical Indicators

- **MA50**: $175.20 (Above)
- **MA200**: $168.80 (Above)
- **RSI**: 62.5 (Slightly Overbought)
- **MACD**: Bullish crossover

## Recommendation

***Strong Buy*** with target price of **$195.00**

> Risk: Trade tensions may impact supply chain

\`\`\`javascript
const entryPrice = 178.35;
const stopLoss = 172.00;
const targetPrice = 195.00;
const riskReward = (targetPrice - entryPrice) / (entryPrice - stopLoss);
// Risk/Reward ratio: 2.1:1
\`\`\`

![FPT Chart](https://example.com/charts/fpt.png)`;

    const result = markdownToPlainText(usReport);

    expect(result).toContain('Vietcombank JSC');
    expect(result).toContain('FPT');
    expect(result).toContain('178.35');
    expect(result).toContain('2.8T');
    expect(result).toContain('Strong Buy');
    expect(result).toContain('195.00');
    expect(result).toContain('Risk/Reward ratio');
  });

  it('handles market review report with multiple stocks', () => {
    const marketReview = `# Thị trường Việt Nam điểm lại

## Diễn biến chỉ số

| Chỉ số | Đóng cửa | Biến động | Giá trị giao dịch |
|------|------|--------|--------|
| VN-Index | 3050.32 | +0.85% | 4285 tỷ |
| HNX-Index | 9850.45 | +1.12% | 5250 tỷ |
| UPCoM-Index | 1950.28 | +1.45% | 2180 tỷ |

## Nhóm ngành nổi bật

1. **Trí tuệ nhân tạo** 🤖
   - Lý do: đột phá công nghệ mô hình lớn
   - Dẫn dắt: FPT, ELC

2. **Xe năng lượng mới** 🚗
   - Lý do: doanh số vượt kỳ vọng
   - Dẫn dắt: VinFast, HHS

3. **Bán dẫn** 💾
   - Lý do: thay thế nội địa tăng tốc
   - Dẫn dắt: FPT, CMG

## Dòng tiền

- **Khối ngoại**: +85.5 tỷ
- **Ký quỹ**: +32.8 tỷ
- **Dòng tiền lớn**: Dòng tiền vào ròng 156.8 tỷ

## Triển vọng

> Vùng dao động dự kiến ngày mai: 3040-3065

**Chiến lược**: theo dõi nhóm công nghệ, kiểm soát tỷ trọng`;

    const result = markdownToPlainText(marketReview);

    expect(result).toContain('Thị trường Việt Nam điểm lại');
    expect(result).toContain('VN-Index');
    expect(result).toContain('3050.32');
    expect(result).toContain('Trí tuệ nhân tạo');
    expect(result).toContain('VinFast');
    expect(result).toContain('Khối ngoại');
    expect(result).toContain('85.5 tỷ');
    expect(result).toContain('3040-3065');
  });

  it('handles report with special characters and formulas', () => {
    const report = `# Tính chỉ báo kỹ thuật

## MACD Tính toán

\`\`\`python
# MACD = EMA(12) - EMA(26)
# Signal = EMA(MACD, 9)
# Histogram = MACD - Signal

def calculate_macd(prices, fast=12, slow=26, signal=9):
    ema_fast = prices.ewm(span=fast).mean()
    ema_slow = prices.ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal).mean()
    return macd, signal_line
\`\`\`

## RSI Công thức

$$RSI = 100 - \frac{100}{1 + RS}$$

Trong đó: 
- RS = Mức tăng trung bình / Mức giảm trung bình
- Chu kỳ: mặc định 14 ngày

## Dải Bollinger

- **Dải giữa** = MA(20)
- **Dải trên** = MA(20) + 2 × STD(20)
- **Dải dưới** = MA(20) - 2 × STD(20)

> Giá hiện tại gần dải trên, chú ý rủi ro điều chỉnh`;

    const result = markdownToPlainText(report);

    expect(result).toContain('MACD Tính toán');
    expect(result).toContain('EMA(12) - EMA(26)');
    expect(result).toContain('RSI');
    expect(result).toContain('Dải Bollinger');
    expect(result).toContain('MA(20)');
    expect(result).toContain('chú ý rủi ro điều chỉnh');
  });

  it('handles report with code snippets in multiple languages', () => {
    const report = `# Mã backtest chiến lược

## Python Chiến lược

\`\`\`python
import pandas as pd
import numpy as np

def moving_average_strategy(data, short=5, long=20):
    signals = pd.DataFrame(index=data.index)
    signals['signal'] = 0

    signals['short_ma'] = data['close'].rolling(window=short).mean()
    signals['long_ma'] = data['close'].rolling(window=long).mean()

    signals.loc[signals['short_ma'] > signals['long_ma'], 'signal'] = 1
    signals.loc[signals['short_ma'] < signals['long_ma'], 'signal'] = -1

    return signals
\`\`\`

Đoạn mã trên có thể dùng trực tiếp để backtest chiến lược.`;

    const result = markdownToPlainText(report);

    // Verify key content is preserved
    expect(result).toContain('Mã backtest chiến lược');
    expect(result).toContain('Python Chiến lược');
    expect(result).toContain('Đoạn mã trên có thể dùng trực tiếp để backtest chiến lược');

    // Verify code content is preserved
    expect(result).toContain('import pandas');
    expect(result).toContain('moving_average_strategy');
  });

  it('handles edge case: very long stock code list', () => {
    const stockList = `# Danh sách rổ cổ phiếu

## Thành phần VN30 (một phần)

| Mã | Tên | Giá hiện tại | Biến động |
|------|------|------|--------|
| HPG | Hoa Phat | 1680.50 | +0.85% |
| 000858 | Sabeco | 125.30 | +1.20% |
| 600036 | Techcombank | 32.50 | -0.25% |
| VCB | Vietcombank | 11.85 | +0.42% |
| 601318 | Bảo Việt | 45.20 | +0.15% |
| 000333 | Điện Quang | 58.80 | +1.80% |
| 600276 | Dược Hậu Giang | 42.50 | +2.10% |
| 300750 | Vinamilk | 185.30 | +3.20% |
| 688981 | CMC Corp | 52.80 | +4.50% |
| 601012 | PC1 | 25.60 | -1.20% |

## Điều kiện lọc

- **Vốn hoá**: > 500 tỷ
- **PE**: 10-50
- **ROE**: > 15%
- **Tỷ lệ nợ**: < 60%`;

    const result = markdownToPlainText(stockList);

    // Verify all stock codes are preserved
    expect(result).toContain('HPG');
    expect(result).toContain('000858');
    expect(result).toContain('601012');
    expect(result).toContain('Hoa Phat');
    expect(result).toContain('Vinamilk');
    expect(result).toContain('Điều kiện lọc');
    expect(result).toContain('ROE');
  });

  it('handles mixed Vietnamese and English punctuation correctly', () => {
    const text = `# Tóm tắt báo cáo

**Quan điểm chính**: 
1. Ngắn hạn tích cực, giá mục tiêu $195.00
2. Hỗ trợ: $168.50-172.00
3. Kháng cự: $180.50-185.00

"Risk: Trade war impact"

> Cảnh báo rủi ro: căng thẳng thương mại có thể ảnh hưởng xuất khẩu

*Điểm chú ý*: AI chip business growth`;

    const result = markdownToPlainText(text);

    expect(result).toContain('Quan điểm chính');
    expect(result).toContain('Ngắn hạn tích cực');
    expect(result).toContain('195.00');
    expect(result).toContain('Risk: Trade war impact');
    expect(result).toContain('Cảnh báo rủi ro');
    expect(result).toContain('Điểm chú ý');
    expect(result).toContain('AI chip business');
  });

  it('preserves numerical data and percentages accurately', () => {
    const report = `# Báo cáo dữ liệu

## Chỉ báo chính

- Doanh thu: 1,234.56 tỷ
- Lợi nhuận ròng: +23.45%
- Thị phần: 15.67%
- ROE: 18.9%
- Tỷ lệ nợ: 45.2%

## Vùng giá

| Ngày | Mở cửa | Cao nhất | Thấp nhất | Đóng cửa |
|------|------|------|------|------|
| 2024-01-15 | 1680.50 | 1695.30 | 1675.20 | 1688.80 |
| 2024-01-16 | 1688.80 | 1702.50 | 1685.30 | 1698.20 |

Biến động: +1.23% (hôm nay)`;

    const result = markdownToPlainText(report);

    expect(result).toContain('1,234.56');
    expect(result).toContain('23.45%');
    expect(result).toContain('15.67%');
    expect(result).toContain('1680.50');
    expect(result).toContain('1695.30');
    expect(result).toContain('1.23%');
  });
});
