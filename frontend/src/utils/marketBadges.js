/*
Market badge labels for the bet log and performance tables.

Generated programmatically so all totals/halftime/cards/corners lines have
matching badges without manually listing every one. The h2h market is
intentionally absent — it's the implicit default and rendering "1X2"
everywhere would add visual noise.

Exported as a plain object so callers can do `MARKET_BADGE[bet.market]`.
*/

export const MARKET_BADGE = {
    btts: 'BTTS',
    compound: 'Compound',
    double_chance: 'Double',
    ht_result: 'HT 1X2',
};

for (const line of ['0_5', '1_5', '2_5', '3_5', '4_5', '5_5']) {
    MARKET_BADGE[`totals_${line}`] = `O/U ${line.replace('_', '.')}`;
}
for (const line of ['0_5', '1_5', '2_5']) {
    MARKET_BADGE[`ht_totals_${line}`] = `HT O/U ${line.replace('_', '.')}`;
}
for (const line of ['2_5', '3_5', '4_5', '5_5', '6_5']) {
    MARKET_BADGE[`cards_${line}`] = `Cards ${line.replace('_', '.')}`;
}
for (const line of ['7_5', '8_5', '9_5', '10_5', '11_5']) {
    MARKET_BADGE[`corners_${line}`] = `Corners ${line.replace('_', '.')}`;
}
