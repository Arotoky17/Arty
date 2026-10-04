# Investigation bid/ask

Predominantly (a): unchanged flat grid rows decay ATR artificially. No observed timestamp misalignment. Remaining paired large moves are candidates, not proven corruption; compare original ticks before rejecting.

{'bid': {'legacy_count': 135, 'corrected_count': 12, 'padding_atr_false_positives': 123, 'absolute_return_flags': 0, 'absolute_range_flags': 0}, 'ask': {'legacy_count': 136, 'corrected_count': 12, 'padding_atr_false_positives': 124, 'absolute_return_flags': 0, 'absolute_range_flags': 0}}

UTC | côté | open | high | low | close
---|---|---:|---:|---:|---:
2024-01-02T23:00:00+00:00 | bid | 2058.018 | 2059.134 | 2058.018 | 2058.795
2024-01-02T23:00:00+00:00 | ask | 2059.772 | 2059.882 | 2059.352 | 2059.352
2024-01-03T23:00:00+00:00 | bid | 2041.895 | 2042.095 | 2041.698 | 2041.998
2024-01-03T23:00:00+00:00 | ask | 2042.452 | 2042.655 | 2042.352 | 2042.552
2024-01-04T23:00:00+00:00 | bid | 2043.218 | 2043.685 | 2043.118 | 2043.385
2024-01-04T23:00:00+00:00 | ask | 2044.812 | 2045.092 | 2043.902 | 2043.902
2024-01-07T23:00:00+00:00 | bid | 2044.385 | 2045.005 | 2043.745 | 2044.498
2024-01-07T23:00:00+00:00 | ask | 2046.355 | 2046.445 | 2044.602 | 2045.102
2024-01-08T23:00:00+00:00 | bid | 2028.045 | 2028.248 | 2027.848 | 2027.948
2024-01-08T23:00:00+00:00 | ask | 2028.635 | 2028.875 | 2028.435 | 2028.535

Exclude calendar-closed bars from the ATR observation clock; retain all raw rows, old flags and absolute 2% checks. No price interpolation.

Aucun P&L ni changement des données brutes.
