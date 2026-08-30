"""Sistem Önerisi: günün tüm maç ve pazarlarını ölçülmüş karneyle sıralayıp kupon kurar.

Bu modül YENİ bir katmandır; mevcut bülten/radar/sağlam akışlarına dokunmaz.
Farkı: bülten maç başına TEK öneri verir, burada bütün maçların fiyatlanabilen
BÜTÜN pazarları (137 adet) tek havuzda toplanır, ÖLÇÜLMÜŞ güvenilirliğe göre
sıralanır ve hedef orana ulaşan kupon kurulur.

────────────────────────────────────────────────────────────────────────
ÖLÇÜM (deney23) — nasıl yapıldı, neden güvenilir:
  • 8.000 test maçı, eğitim/test AYRIMI ile: model yalnız 01.07.2023'ten
    ÖNCEKİ arşivle kuruldu, karne o tarihten SONRAKİ maçlarda ölçüldü.
    Böylece "geleceği görerek" şişmiş bir karne çıkmadı.
  • analiz.tum_pazarlar() ile üretilen 137 pazarın hepsi her maçta kaydedildi
    (876.040 ölçüm), analiz.pazar_gerceklesti() ile gerçek skora bakıldı.
  • "ayırt gücü" = modelin en güvendiği çeyrek ile en az güvendiği çeyrek
    arasındaki GERÇEK fark. Sıfıra yakınsa model o pazarda maça özel bilgi
    taşımıyor, yalnız lig ortalamasını tekrarlıyor demektir.

SÜZGEÇ (91 pazar geçti, 46 elendi):
  • örneklem ≥ 500
  • kalibrasyon sapması ≤ 3 puan (tüm dağılımda)
  • ayırt gücü ≥ 5 puan
  • ve AYRICA sistemin fiilen oynadığı bölgede (model ≥ %60) sapma ≤ 3.5 puan
    — bir pazar ortalamada kalibre olup tam da bahis yaptığımız bölgede
    bozulabiliyor (ör. EV KORNER ÜST 3.5: genelde iyi, %60+ bölgesinde -5.5).

SAPMA DÜZELTMESİ NEDEN YOK (ölçüldü, reddedildi):
  Bir pazarda ölçülen sistematik sapmayı olasılığa geri eklemek cazip görünüyor
  (kodda KG_DUZELTME örneği var). Sınadık: test dönemi ikiye bölünüp düzeltme
  ilk yarıda öğrenildi, ikinci yarıda uygulandı. Sonuç: 35 pazarda sapmayı
  azalttı, 26 pazarda ARTIRDI — yani yazı-tura. Genelleşmeyen bir düzeltme
  sahte hassasiyettir; uygulanmıyor. Bunun yerine süzgeç sıkı tutuluyor.
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import math

# pazar → {n, dedi, gercek, fark, ayirt, kullanim}. Sayılar deney23 ölçümünden
# birebir üretildi (elle yazılmadı). "kullanim" = modelin ≥%60 dediği bölgedeki
# karne; kullanıcıya gösterilen "gerçekte ne oldu" odur, çünkü sistem yalnız o
# bölgede öneri yapıyor. Küçük örneklemli kullanim satırları gösterilmez.
#
# ÖLÇÜM SIRASINDA YAKALANAN HATA (deney22): ust_alt demeti motora ters
# ((alt, üst)) geçilmişti; ÜST/ALT 2.5 satırları çöp çıkmıştı. Motorun sırası
# (ÜST, ALT) — düzeltilip yeniden ölçüldü.
PAZAR_KARNE: dict[str, dict] = {
    "KORNER ÜST 10.5": {"n": 4538, "dedi": 0.3936, "gercek": 0.3995, "fark": +0.0060, "ayirt": +0.1358, "kullanim": {"n": 3, "dedi": 0.6167, "gercek": 1.0000}},
    "KART ÜST 5.5": {"n": 4892, "dedi": 0.2135, "gercek": 0.2418, "fark": +0.0283, "ayirt": +0.1791, "kullanim": {"n": 1, "dedi": 0.6100, "gercek": 1.0000}},
    "EV ALT 3.5": {"n": 8000, "dedi": 0.9252, "gercek": 0.9295, "fark": +0.0043, "ayirt": +0.0865, "kullanim": {"n": 7954, "dedi": 0.9276, "gercek": 0.9300}},
    "DEP ALT 2.5": {"n": 8000, "dedi": 0.8780, "gercek": 0.8731, "fark": -0.0048, "ayirt": +0.0900, "kullanim": {"n": 7905, "dedi": 0.8822, "gercek": 0.8756}},
    "HND 2:0 1": {"n": 8000, "dedi": 0.8631, "gercek": 0.8686, "fark": +0.0055, "ayirt": +0.1255, "kullanim": {"n": 7848, "dedi": 0.8699, "gercek": 0.8726}},
    "ALT 4.5": {"n": 8000, "dedi": 0.8645, "gercek": 0.8611, "fark": -0.0033, "ayirt": +0.0580, "kullanim": {"n": 7952, "dedi": 0.8666, "gercek": 0.8614}},
    "EV ALT 2.5": {"n": 8000, "dedi": 0.8084, "gercek": 0.8033, "fark": -0.0051, "ayirt": +0.1625, "kullanim": {"n": 7520, "dedi": 0.8278, "gercek": 0.8149}},
    "DEP KORNER ALT 6.5": {"n": 4538, "dedi": 0.8300, "gercek": 0.8041, "fark": -0.0259, "ayirt": +0.1411, "kullanim": {"n": 4523, "dedi": 0.8309, "gercek": 0.8050}},
    "HND 0:2 2": {"n": 8000, "dedi": 0.7711, "gercek": 0.7815, "fark": +0.0104, "ayirt": +0.1775, "kullanim": {"n": 7168, "dedi": 0.8036, "gercek": 0.8023}},
    "2Y GOL VAR": {"n": 5128, "dedi": 0.7771, "gercek": 0.7894, "fark": +0.0123, "ayirt": +0.0671, "kullanim": {"n": 5128, "dedi": 0.7771, "gercek": 0.7894}},
    "EV ÜST 0.5": {"n": 8000, "dedi": 0.7468, "gercek": 0.7689, "fark": +0.0220, "ayirt": +0.1360, "kullanim": {"n": 7354, "dedi": 0.7655, "gercek": 0.7771}},
    "KART ALT 5.5": {"n": 4892, "dedi": 0.7865, "gercek": 0.7582, "fark": -0.0283, "ayirt": +0.1774, "kullanim": {"n": 4468, "dedi": 0.8082, "gercek": 0.7755}},
    "KART ÜST 2.5": {"n": 4892, "dedi": 0.7314, "gercek": 0.7598, "fark": +0.0284, "ayirt": +0.1406, "kullanim": {"n": 4414, "dedi": 0.7499, "gercek": 0.7703}},
    "ÜST 1.5": {"n": 8000, "dedi": 0.7333, "gercek": 0.7511, "fark": +0.0178, "ayirt": +0.1045, "kullanim": {"n": 7410, "dedi": 0.7483, "gercek": 0.7549}},
    "ÇŞ 12": {"n": 8000, "dedi": 0.7387, "gercek": 0.7390, "fark": +0.0003, "ayirt": +0.0650, "kullanim": {"n": 7985, "dedi": 0.7391, "gercek": 0.7391}},
    "ÇŞ 1X": {"n": 8000, "dedi": 0.6958, "gercek": 0.6987, "fark": +0.0030, "ayirt": +0.2110, "kullanim": {"n": 6208, "dedi": 0.7526, "gercek": 0.7334}},
    "HND 1:0 1": {"n": 8000, "dedi": 0.6958, "gercek": 0.6987, "fark": +0.0030, "ayirt": +0.2110, "kullanim": {"n": 6208, "dedi": 0.7526, "gercek": 0.7334}},
    "MS2": {"n": 8000, "dedi": 0.3030, "gercek": 0.3013, "fark": -0.0018, "ayirt": +0.3545, "kullanim": {"n": 235, "dedi": 0.6650, "gercek": 0.7277}},
    "ALT 3.5": {"n": 8000, "dedi": 0.7252, "gercek": 0.7096, "fark": -0.0156, "ayirt": +0.0920, "kullanim": {"n": 7150, "dedi": 0.7484, "gercek": 0.7173}},
    "İY 0.5 ÜST": {"n": 5128, "dedi": 0.6930, "gercek": 0.7147, "fark": +0.0217, "ayirt": +0.0757, "kullanim": {"n": 5111, "dedi": 0.6933, "gercek": 0.7153}},
    "MS1": {"n": 8000, "dedi": 0.4377, "gercek": 0.4377, "fark": +0.0000, "ayirt": +0.4125, "kullanim": {"n": 1015, "dedi": 0.6858, "gercek": 0.7153}},
    "DEP KORNER ALT 5.5": {"n": 4538, "dedi": 0.7058, "gercek": 0.6963, "fark": -0.0094, "ayirt": +0.1720, "kullanim": {"n": 4101, "dedi": 0.7217, "gercek": 0.7108}},
    "DEP ÜST 0.5": {"n": 8000, "dedi": 0.6668, "gercek": 0.6884, "fark": +0.0216, "ayirt": +0.1155, "kullanim": {"n": 5866, "dedi": 0.7191, "gercek": 0.7092}},
    "KORNER ALT 11.5": {"n": 4538, "dedi": 0.7161, "gercek": 0.7003, "fark": -0.0158, "ayirt": +0.1173, "kullanim": {"n": 4449, "dedi": 0.7189, "gercek": 0.6995}},
    "EV KORNER ALT 6.5": {"n": 4538, "dedi": 0.7090, "gercek": 0.6805, "fark": -0.0286, "ayirt": +0.1631, "kullanim": {"n": 4195, "dedi": 0.7227, "gercek": 0.6937}},
    "HER İKİ YARI GOL VAR": {"n": 5128, "dedi": 0.5463, "gercek": 0.5702, "fark": +0.0239, "ayirt": +0.1084, "kullanim": {"n": 312, "dedi": 0.6164, "gercek": 0.6795}},
    "İY 1.5 ALT": {"n": 5128, "dedi": 0.6739, "gercek": 0.6605, "fark": -0.0134, "ayirt": +0.0850, "kullanim": {"n": 4383, "dedi": 0.6942, "gercek": 0.6747}},
    "EV KORNER ALT 5.5": {"n": 4538, "dedi": 0.5584, "gercek": 0.5650, "fark": +0.0066, "ayirt": +0.1755, "kullanim": {"n": 1226, "dedi": 0.6604, "gercek": 0.6639}},
    "DEP KORNER ALT 4.5": {"n": 4538, "dedi": 0.5400, "gercek": 0.5538, "fark": +0.0138, "ayirt": +0.1825, "kullanim": {"n": 1136, "dedi": 0.6555, "gercek": 0.6585}},
    "İY 1": {"n": 5128, "dedi": 0.3213, "gercek": 0.3481, "fark": +0.0268, "ayirt": +0.1716, "kullanim": {"n": 80, "dedi": 0.6523, "gercek": 0.6500}},
    "KORNER ÜST 8.5": {"n": 4538, "dedi": 0.6420, "gercek": 0.6322, "fark": -0.0098, "ayirt": +0.1005, "kullanim": {"n": 3500, "dedi": 0.6702, "gercek": 0.6491}},
    "ÜST 2.5": {"n": 8000, "dedi": 0.4968, "gercek": 0.5138, "fark": +0.0170, "ayirt": +0.1750, "kullanim": {"n": 970, "dedi": 0.6537, "gercek": 0.6485}},
    "EV HER İKİ YARIDA GOL ATAR": {"n": 5128, "dedi": 0.2625, "gercek": 0.2820, "fark": +0.0195, "ayirt": +0.1404, "kullanim": {"n": 51, "dedi": 0.6447, "gercek": 0.6471}},
    "KORNER ALT 10.5": {"n": 4538, "dedi": 0.6064, "gercek": 0.6005, "fark": -0.0060, "ayirt": +0.1376, "kullanim": {"n": 2289, "dedi": 0.6602, "gercek": 0.6453}},
    "EV KORNER ÜST 5.5": {"n": 4538, "dedi": 0.4416, "gercek": 0.4350, "fark": -0.0066, "ayirt": +0.1817, "kullanim": {"n": 192, "dedi": 0.6460, "gercek": 0.6406}},
    "İY/MS 1/1": {"n": 5128, "dedi": 0.2644, "gercek": 0.2679, "fark": +0.0035, "ayirt": +0.1841, "kullanim": {"n": 65, "dedi": 0.6446, "gercek": 0.6308}},
    "DEP KORNER ÜST 4.5": {"n": 4538, "dedi": 0.4600, "gercek": 0.4462, "fark": -0.0138, "ayirt": +0.1869, "kullanim": {"n": 213, "dedi": 0.6485, "gercek": 0.6244}},
    "2Y 1": {"n": 5128, "dedi": 0.3559, "gercek": 0.3672, "fark": +0.0113, "ayirt": +0.1880, "kullanim": {"n": 206, "dedi": 0.6651, "gercek": 0.6068}},
    "KORNER ALT 9.5": {"n": 4538, "dedi": 0.4836, "gercek": 0.4855, "fark": +0.0019, "ayirt": +0.1138, "kullanim": {"n": 325, "dedi": 0.6405, "gercek": 0.6062}},
    "2Y 2": {"n": 5128, "dedi": 0.2653, "gercek": 0.2845, "fark": +0.0192, "ayirt": +0.1513, "kullanim": {"n": 24, "dedi": 0.6452, "gercek": 0.5833}},
    "KORNER ÜST 9.5": {"n": 4538, "dedi": 0.5164, "gercek": 0.5145, "fark": -0.0019, "ayirt": +0.1138, "kullanim": {"n": 450, "dedi": 0.6245, "gercek": 0.5733}},
    "2 ve ÜST 2.5": {"n": 8000, "dedi": 0.1719, "gercek": 0.1804, "fark": +0.0085, "ayirt": +0.1360, "kullanim": {"n": 14, "dedi": 0.6592, "gercek": 0.5714}},
    "1 ve ÜST 1.5": {"n": 8000, "dedi": 0.3347, "gercek": 0.3377, "fark": +0.0030, "ayirt": +0.2320, "kullanim": {"n": 455, "dedi": 0.6855, "gercek": 0.5648}},
    "2Y 1.5 ÜST": {"n": 5128, "dedi": 0.4350, "gercek": 0.4520, "fark": +0.0171, "ayirt": +0.0647, "kullanim": {"n": 174, "dedi": 0.6437, "gercek": 0.5632}},
    "DEP ÜST 1.5": {"n": 8000, "dedi": 0.3203, "gercek": 0.3387, "fark": +0.0185, "ayirt": +0.1545, "kullanim": {"n": 217, "dedi": 0.6711, "gercek": 0.5576}},
    "HND 0:1 1": {"n": 8000, "dedi": 0.2289, "gercek": 0.2185, "fark": -0.0104, "ayirt": +0.1775, "kullanim": {"n": 136, "dedi": 0.6784, "gercek": 0.5294}},
    "DEP ALT 0.5": {"n": 8000, "dedi": 0.3332, "gercek": 0.3116, "fark": -0.0216, "ayirt": +0.1155, "kullanim": {"n": 141, "dedi": 0.6468, "gercek": 0.4823}},
    "2 ve ÜST 1.5": {"n": 8000, "dedi": 0.2240, "gercek": 0.2258, "fark": +0.0018, "ayirt": +0.1720, "kullanim": {"n": 70, "dedi": 0.6684, "gercek": 0.4714}},
    "1 ve ÜST 2.5": {"n": 8000, "dedi": 0.2570, "gercek": 0.2662, "fark": +0.0092, "ayirt": +0.1890, "kullanim": {"n": 126, "dedi": 0.6750, "gercek": 0.4603}},
    "HER İKİ YARI GOL YOK": {"n": 5128, "dedi": 0.4537, "gercek": 0.4298, "fark": -0.0239, "ayirt": +0.1084, "kullanim": None},
    "ÜST 3.5": {"n": 8000, "dedi": 0.2748, "gercek": 0.2904, "fark": +0.0156, "ayirt": +0.0920, "kullanim": {"n": 45, "dedi": 0.6871, "gercek": 0.3556}},
    "EV ÜST 2.5": {"n": 8000, "dedi": 0.1916, "gercek": 0.1968, "fark": +0.0051, "ayirt": +0.1625, "kullanim": {"n": 64, "dedi": 0.6745, "gercek": 0.3438}},
    "HND 0:2 1": {"n": 8000, "dedi": 0.1002, "gercek": 0.0946, "fark": -0.0056, "ayirt": +0.1100, "kullanim": {"n": 12, "dedi": 0.6658, "gercek": 0.3333}},
    "EV HER İKİ YARIYI KAZANIR": {"n": 5128, "dedi": 0.1269, "gercek": 0.1305, "fark": +0.0036, "ayirt": +0.1209, "kullanim": {"n": 3, "dedi": 0.6360, "gercek": 0.3333}},
    "DEP HER İKİ YARIDA GOL ATAR": {"n": 5128, "dedi": 0.1947, "gercek": 0.2100, "fark": +0.0153, "ayirt": +0.0991, "kullanim": {"n": 3, "dedi": 0.6355, "gercek": 0.3333}},
    "İY/MS 2/2": {"n": 5128, "dedi": 0.1815, "gercek": 0.1753, "fark": -0.0062, "ayirt": +0.1326, "kullanim": {"n": 3, "dedi": 0.6190, "gercek": 0.3333}},
    "KORNER ALT 8.5": {"n": 4538, "dedi": 0.3580, "gercek": 0.3678, "fark": +0.0098, "ayirt": +0.1005, "kullanim": {"n": 3, "dedi": 0.6330, "gercek": 0.3333}},
    "EV KORNER ÜST 6.5": {"n": 4538, "dedi": 0.2910, "gercek": 0.3195, "fark": +0.0286, "ayirt": +0.1623, "kullanim": {"n": 9, "dedi": 0.6334, "gercek": 0.3333}},
    "DEP KORNER ÜST 5.5": {"n": 4538, "dedi": 0.2942, "gercek": 0.3037, "fark": +0.0094, "ayirt": +0.1755, "kullanim": {"n": 6, "dedi": 0.6136, "gercek": 0.3333}},
    "ALT 1.5": {"n": 8000, "dedi": 0.2667, "gercek": 0.2489, "fark": -0.0178, "ayirt": +0.1045, "kullanim": {"n": 16, "dedi": 0.7089, "gercek": 0.3125}},
    "EV ALT 0.5": {"n": 8000, "dedi": 0.2532, "gercek": 0.2311, "fark": -0.0220, "ayirt": +0.1360, "kullanim": {"n": 26, "dedi": 0.6680, "gercek": 0.3077}},
    "1 ve ALT 3.5": {"n": 8000, "dedi": 0.2984, "gercek": 0.2998, "fark": +0.0014, "ayirt": +0.1180, "kullanim": None},
    "KORNER ÜST 11.5": {"n": 4538, "dedi": 0.2839, "gercek": 0.2997, "fark": +0.0158, "ayirt": +0.1182, "kullanim": None},
    "İY 0.5 ALT": {"n": 5128, "dedi": 0.3070, "gercek": 0.2853, "fark": -0.0217, "ayirt": +0.0757, "kullanim": None},
    "MS0": {"n": 8000, "dedi": 0.2592, "gercek": 0.2610, "fark": +0.0018, "ayirt": +0.1075, "kullanim": None},
    "İY 2": {"n": 5128, "dedi": 0.2428, "gercek": 0.2560, "fark": +0.0132, "ayirt": +0.1225, "kullanim": {"n": 4, "dedi": 0.6354, "gercek": 0.2500}},
    "KART ALT 2.5": {"n": 4892, "dedi": 0.2686, "gercek": 0.2402, "fark": -0.0284, "ayirt": +0.1382, "kullanim": None},
    "ÜST 4.5": {"n": 8000, "dedi": 0.1355, "gercek": 0.1389, "fark": +0.0033, "ayirt": +0.0580, "kullanim": {"n": 9, "dedi": 0.6914, "gercek": 0.2222}},
    "HND 0:1 0": {"n": 8000, "dedi": 0.2056, "gercek": 0.2193, "fark": +0.0136, "ayirt": +0.0665, "kullanim": None},
    "2 ve ALT 3.5": {"n": 8000, "dedi": 0.2205, "gercek": 0.2160, "fark": -0.0045, "ayirt": +0.1390, "kullanim": None},
    "HND 1:0 2": {"n": 8000, "dedi": 0.1369, "gercek": 0.1314, "fark": -0.0055, "ayirt": +0.1255, "kullanim": {"n": 14, "dedi": 0.6669, "gercek": 0.2143}},
    "2Y GOL YOK": {"n": 5128, "dedi": 0.2229, "gercek": 0.2106, "fark": -0.0123, "ayirt": +0.0671, "kullanim": None},
    "EV ÜST 3.5": {"n": 8000, "dedi": 0.0748, "gercek": 0.0705, "fark": -0.0043, "ayirt": +0.0865, "kullanim": {"n": 5, "dedi": 0.7015, "gercek": 0.2000}},
    "DEP ÜST 2.5": {"n": 8000, "dedi": 0.1220, "gercek": 0.1269, "fark": +0.0048, "ayirt": +0.0900, "kullanim": {"n": 5, "dedi": 0.6660, "gercek": 0.2000}},
    "DEP KORNER ÜST 6.5": {"n": 4538, "dedi": 0.1700, "gercek": 0.1959, "fark": +0.0259, "ayirt": +0.1411, "kullanim": None},
    "0 ve ALT 2.5": {"n": 8000, "dedi": 0.2064, "gercek": 0.1939, "fark": -0.0125, "ayirt": +0.0540, "kullanim": None},
    "0 ve ALT 3.5": {"n": 8000, "dedi": 0.2064, "gercek": 0.1939, "fark": -0.0125, "ayirt": +0.0540, "kullanim": None},
    "0 ve ÜST 1.5": {"n": 8000, "dedi": 0.1746, "gercek": 0.1876, "fark": +0.0130, "ayirt": +0.0515, "kullanim": None},
    "1 ve ALT 2.5": {"n": 8000, "dedi": 0.1775, "gercek": 0.1715, "fark": -0.0060, "ayirt": +0.0650, "kullanim": None},
    "HND 1:0 0": {"n": 8000, "dedi": 0.1673, "gercek": 0.1699, "fark": +0.0026, "ayirt": +0.0875, "kullanim": None},
    "İY 1.5 ÜST": {"n": 5128, "dedi": 0.3261, "gercek": 0.3395, "fark": +0.0134, "ayirt": +0.0850, "kullanim": {"n": 6, "dedi": 0.6499, "gercek": 0.1667}},
    "İY/MS 0/0": {"n": 5128, "dedi": 0.1576, "gercek": 0.1476, "fark": -0.0100, "ayirt": +0.0554, "kullanim": None},
    "İY/MS 0/1": {"n": 5128, "dedi": 0.1512, "gercek": 0.1431, "fark": -0.0080, "ayirt": +0.0585, "kullanim": None},
    "1 ve ÜST 3.5": {"n": 8000, "dedi": 0.1361, "gercek": 0.1380, "fark": +0.0019, "ayirt": +0.1225, "kullanim": {"n": 14, "dedi": 0.6859, "gercek": 0.1429}},
    "HND 0:2 0": {"n": 8000, "dedi": 0.1287, "gercek": 0.1239, "fark": -0.0048, "ayirt": +0.0685, "kullanim": None},
    "2 ve ALT 2.5": {"n": 8000, "dedi": 0.1323, "gercek": 0.1209, "fark": -0.0114, "ayirt": +0.0715, "kullanim": None},
    "İY/MS 0/2": {"n": 5128, "dedi": 0.1157, "gercek": 0.1051, "fark": -0.0106, "ayirt": +0.0593, "kullanim": None},
    "HND 2:0 0": {"n": 8000, "dedi": 0.0867, "gercek": 0.0808, "fark": -0.0059, "ayirt": +0.0620, "kullanim": None},
    "DEP HER İKİ YARIYI KAZANIR": {"n": 5128, "dedi": 0.0739, "gercek": 0.0788, "fark": +0.0049, "ayirt": +0.0819, "kullanim": None},
    "HND 2:0 2": {"n": 8000, "dedi": 0.0502, "gercek": 0.0506, "fark": +0.0004, "ayirt": +0.0680, "kullanim": {"n": 1, "dedi": 0.7582, "gercek": 0.0000}},
    "2 ve ÜST 3.5": {"n": 8000, "dedi": 0.0837, "gercek": 0.0853, "fark": +0.0015, "ayirt": +0.0675, "kullanim": {"n": 1, "dedi": 0.6954, "gercek": 0.0000}},
    "ÜST 5.5": {"n": 8000, "dedi": 0.0592, "gercek": 0.0585, "fark": -0.0007, "ayirt": +0.0385, "kullanim": {"n": 1, "dedi": 0.6003, "gercek": 1.0000}},
    "DEP ALT 3.5": {"n": 8000, "dedi": 0.9605, "gercek": 0.9596, "fark": -0.0009, "ayirt": +0.0420, "kullanim": {"n": 7997, "dedi": 0.9607, "gercek": 0.9596}},
    "ALT 5.5": {"n": 8000, "dedi": 0.9408, "gercek": 0.9415, "fark": +0.0007, "ayirt": +0.0385, "kullanim": {"n": 7991, "dedi": 0.9413, "gercek": 0.9417}},
    "ÜST 0.5": {"n": 8000, "dedi": 0.9134, "gercek": 0.9266, "fark": +0.0133, "ayirt": +0.0375, "kullanim": {"n": 7995, "dedi": 0.9136, "gercek": 0.9266}},
    "İY 2.5 ALT": {"n": 5128, "dedi": 0.8858, "gercek": 0.8766, "fark": -0.0093, "ayirt": +0.0398, "kullanim": {"n": 5127, "dedi": 0.8859, "gercek": 0.8765}},
    "İY KG YOK": {"n": 5128, "dedi": 0.8161, "gercek": 0.8087, "fark": -0.0074, "ayirt": +0.0312, "kullanim": {"n": 5124, "dedi": 0.8164, "gercek": 0.8085}},
    "2Y 2.5 ALT": {"n": 5128, "dedi": 0.8136, "gercek": 0.8066, "fark": -0.0070, "ayirt": +0.0499, "kullanim": {"n": 5089, "dedi": 0.8155, "gercek": 0.8066}},
    "2Y 0.5 ÜST": {"n": 5128, "dedi": 0.7555, "gercek": 0.7894, "fark": +0.0339, "ayirt": +0.0679, "kullanim": {"n": 5083, "dedi": 0.7570, "gercek": 0.7885}},
    "DEP KORNER ALT 3.5": {"n": 4538, "dedi": 0.3537, "gercek": 0.4041, "fark": +0.0505, "ayirt": +0.1720, "kullanim": {"n": 40, "dedi": 0.6215, "gercek": 0.7500}},
    "2Y KG YOK": {"n": 5128, "dedi": 0.7436, "gercek": 0.7338, "fark": -0.0098, "ayirt": +0.0039, "kullanim": {"n": 5070, "dedi": 0.7456, "gercek": 0.7343}},
    "EV KORNER ÜST 3.5": {"n": 4538, "dedi": 0.7725, "gercek": 0.7173, "fark": -0.0552, "ayirt": +0.1455, "kullanim": {"n": 4486, "dedi": 0.7748, "gercek": 0.7196}},
    "EV KORNER ALT 4.5": {"n": 4538, "dedi": 0.3885, "gercek": 0.4273, "fark": +0.0388, "ayirt": +0.1702, "kullanim": {"n": 52, "dedi": 0.6281, "gercek": 0.7115}},
    "DEP ALT 1.5": {"n": 8000, "dedi": 0.6797, "gercek": 0.6613, "fark": -0.0185, "ayirt": +0.1550, "kullanim": {"n": 6030, "dedi": 0.7359, "gercek": 0.6892}},
    "KART ÜST 3.5": {"n": 4892, "dedi": 0.5392, "gercek": 0.5814, "fark": +0.0422, "ayirt": +0.2052, "kullanim": {"n": 1676, "dedi": 0.7068, "gercek": 0.6802}},
    "KART ALT 4.5": {"n": 4892, "dedi": 0.6433, "gercek": 0.6094, "fark": -0.0339, "ayirt": +0.1897, "kullanim": {"n": 3191, "dedi": 0.7334, "gercek": 0.6622}},
    "ÇŞ X2": {"n": 8000, "dedi": 0.5655, "gercek": 0.5623, "fark": -0.0032, "ayirt": +0.2455, "kullanim": {"n": 3477, "dedi": 0.7042, "gercek": 0.6388}},
    "HND 0:1 2": {"n": 8000, "dedi": 0.5655, "gercek": 0.5623, "fark": -0.0032, "ayirt": +0.2455, "kullanim": {"n": 3477, "dedi": 0.7042, "gercek": 0.6388}},
    "DEP KORNER ÜST 3.5": {"n": 4538, "dedi": 0.6463, "gercek": 0.5959, "fark": -0.0505, "ayirt": +0.1728, "kullanim": {"n": 3402, "dedi": 0.6826, "gercek": 0.6267}},
    "EV KORNER ÜST 4.5": {"n": 4538, "dedi": 0.6115, "gercek": 0.5727, "fark": -0.0388, "ayirt": +0.1746, "kullanim": {"n": 2571, "dedi": 0.6661, "gercek": 0.6239}},
    "EV ALT 1.5": {"n": 8000, "dedi": 0.5792, "gercek": 0.5623, "fark": -0.0169, "ayirt": +0.2175, "kullanim": {"n": 3785, "dedi": 0.6989, "gercek": 0.6232}},
    "EV ÜST 1.5": {"n": 8000, "dedi": 0.4208, "gercek": 0.4377, "fark": +0.0169, "ayirt": +0.2175, "kullanim": {"n": 950, "dedi": 0.6852, "gercek": 0.6137}},
    "KG VAR": {"n": 8000, "dedi": 0.5003, "gercek": 0.5306, "fark": +0.0304, "ayirt": +0.0605, "kullanim": {"n": 979, "dedi": 0.6390, "gercek": 0.5832}},
    "2Y 1.5 ALT": {"n": 5128, "dedi": 0.5650, "gercek": 0.5480, "fark": -0.0171, "ayirt": +0.0647, "kullanim": {"n": 1811, "dedi": 0.6554, "gercek": 0.5776}},
    "KART ÜST 4.5": {"n": 4892, "dedi": 0.3567, "gercek": 0.3906, "fark": +0.0339, "ayirt": +0.1954, "kullanim": {"n": 268, "dedi": 0.6321, "gercek": 0.5597}},
    "ALT 2.5": {"n": 8000, "dedi": 0.5032, "gercek": 0.4863, "fark": -0.0170, "ayirt": +0.1750, "kullanim": {"n": 1038, "dedi": 0.6549, "gercek": 0.5491}},
    "KART ALT 3.5": {"n": 4892, "dedi": 0.4608, "gercek": 0.4186, "fark": -0.0422, "ayirt": +0.2028, "kullanim": {"n": 767, "dedi": 0.6437, "gercek": 0.5241}},
    "KG YOK": {"n": 8000, "dedi": 0.4997, "gercek": 0.4694, "fark": -0.0304, "ayirt": +0.0605, "kullanim": {"n": 999, "dedi": 0.6497, "gercek": 0.5055}},
    "İY 0": {"n": 5128, "dedi": 0.4359, "gercek": 0.3959, "fark": -0.0400, "ayirt": +0.0842, "kullanim": {"n": 4, "dedi": 0.6059, "gercek": 0.5000}},
    "2Y 0": {"n": 5128, "dedi": 0.3788, "gercek": 0.3483, "fark": -0.0305, "ayirt": +0.0835, "kullanim": None},
    "EV KORNER ALT 3.5": {"n": 4538, "dedi": 0.2275, "gercek": 0.2827, "fark": +0.0552, "ayirt": +0.1446, "kullanim": None},
    "2Y 0.5 ALT": {"n": 5128, "dedi": 0.2445, "gercek": 0.2106, "fark": -0.0339, "ayirt": +0.0679, "kullanim": None},
    "2Y 2.5 ÜST": {"n": 5128, "dedi": 0.1864, "gercek": 0.1934, "fark": +0.0070, "ayirt": +0.0499, "kullanim": None},
    "İY KG VAR": {"n": 5128, "dedi": 0.1839, "gercek": 0.1913, "fark": +0.0074, "ayirt": +0.0312, "kullanim": None},
    "İY 2.5 ÜST": {"n": 5128, "dedi": 0.1142, "gercek": 0.1234, "fark": +0.0093, "ayirt": +0.0398, "kullanim": None},
    "1 ve ALT 1.5": {"n": 8000, "dedi": 0.0998, "gercek": 0.1000, "fark": +0.0002, "ayirt": +0.0405, "kullanim": None},
    "2 ve ALT 1.5": {"n": 8000, "dedi": 0.0802, "gercek": 0.0755, "fark": -0.0047, "ayirt": +0.0460, "kullanim": None},
    "ALT 0.5": {"n": 8000, "dedi": 0.0866, "gercek": 0.0734, "fark": -0.0133, "ayirt": +0.0375, "kullanim": None},
    "0 ve ALT 1.5": {"n": 8000, "dedi": 0.0866, "gercek": 0.0734, "fark": -0.0133, "ayirt": +0.0375, "kullanim": None},
    "0 ve ÜST 2.5": {"n": 8000, "dedi": 0.0549, "gercek": 0.0671, "fark": +0.0122, "ayirt": +0.0240, "kullanim": None},
    "0 ve ÜST 3.5": {"n": 8000, "dedi": 0.0549, "gercek": 0.0671, "fark": +0.0122, "ayirt": +0.0240, "kullanim": None},
    "İY/MS 1/0": {"n": 5128, "dedi": 0.0452, "gercek": 0.0587, "fark": +0.0135, "ayirt": +0.0086, "kullanim": None},
    "İY/MS 2/0": {"n": 5128, "dedi": 0.0452, "gercek": 0.0530, "fark": +0.0078, "ayirt": +0.0250, "kullanim": None},
    "İY/MS 2/1": {"n": 5128, "dedi": 0.0218, "gercek": 0.0277, "fark": +0.0059, "ayirt": +0.0195, "kullanim": None},
    "İY/MS 1/2": {"n": 5128, "dedi": 0.0174, "gercek": 0.0215, "fark": +0.0041, "ayirt": +0.0117, "kullanim": None},
    "DEP ÜST 3.5": {"n": 8000, "dedi": 0.0395, "gercek": 0.0404, "fark": +0.0009, "ayirt": +0.0420, "kullanim": {"n": 1, "dedi": 0.6450, "gercek": 0.0000}},
    "2Y KG VAR": {"n": 5128, "dedi": 0.2564, "gercek": 0.2662, "fark": +0.0098, "ayirt": +0.0047, "kullanim": {"n": 1, "dedi": 0.6110, "gercek": 0.0000}},
}

# KORNER NEDEN BURADA VAR: daha önce korner "kalıp" (oran deseni) yaklaşımıyla
# denenmiş ve REDDEDİLMİŞTİ — 9.5 üstü için %57.9 diyip %51.1 tutturmuştu.
# Burada ölçülen BAŞKA bir yaklaşım: takımların zaman ağırlıklı korner
# üretim/yeme oranlarından Poisson beklentisi (analiz.korner_beklentisi).
# İlk kez ölçüldü ve kalibrasyonu iyi çıktı (sapma ≤1.8 puan, ayırt +9…+11).
# Yani reddedilen kalıp yöntemi değil, bu yöntem kullanılıyor.

# ÖLÇÜLEN STRATEJİ KARNESİ (deney24) — sekmedeki "gerçekte ne oldu" satırı buradan.
#
# Nasıl ölçüldü: 305 GÜN seçildi (üretimdeki gibi bütün bir günün bülteni),
# 14.031 maç tarandı, her gün için ÜRETİMDEKİ havuz_kur + kupon_kur çağrıldı ve
# kurulan kuponun gerçekten tutup tutmadığına bakıldı. 1.812 kupon simüle edildi.
#
# Ölçülen asıl soru "kâr var mı" değil — bacakların çoğunun gerçek fiyatı
# bültende olmadığı için ROI ölçülemez. Ölçülebilen ve asıl önemli olan şu:
# SİSTEMİN SÖYLEDİĞİ OLASILIK DOĞRU MU?
#
#   hedef 2.00 · eşik %55: 302 kupon · dedi %52.6 · gerçek %50.7  (-2.0 puan)
#   hedef 2.00 · eşik %60: 298 kupon · dedi %51.6 · gerçek %52.0  (+0.4 puan) ← en iyi
#   hedef 2.00 · eşik %65: 270 kupon · dedi %50.8 · gerçek %49.6  (-1.2 puan)
#   hedef 2.00 · eşik %70: 241 kupon · dedi %50.5 · gerçek %44.8  (-5.7 puan)
#   hedef 3.00 · eşik %55: 295 kupon · dedi %35.6 · gerçek %36.6  (+1.0 puan)
#   hedef 3.00 · eşik %60: 241 kupon · dedi %34.8 · gerçek %34.0  (-0.7 puan)
#
# ESKİ İDDİA ÇÜRÜDÜ: önceki (dar havuzlu) ölçüme dayanarak "eşiği yükseltmek
# isabeti artırır" yazmıştık. Geniş havuzda TERSİ çıktı: %70 eşikte isabet
# %44.8'e düşüyor. Sebebi anlaşılır — eşik yükseldikçe havuz daralıyor ve
# kupon, ölçümü zayıf uç pazarlara kayıyor. Varsayılan %60'ta bırakıldı.
STRATEJI_KARNE = {
    (2.0, 0.55): {"n": 302, "dedi": 0.526, "gercek": 0.507},
    (2.0, 0.60): {"n": 298, "dedi": 0.516, "gercek": 0.520},
    (2.0, 0.65): {"n": 270, "dedi": 0.508, "gercek": 0.496},
    (2.0, 0.70): {"n": 241, "dedi": 0.505, "gercek": 0.448},
    (3.0, 0.55): {"n": 295, "dedi": 0.356, "gercek": 0.366},
    (3.0, 0.60): {"n": 241, "dedi": 0.348, "gercek": 0.340},
}


def strateji_karne(hedef: float, esik: float) -> dict | None:
    """Kullanıcının ayarına en yakın ölçülmüş hücre (hedef önce, sonra eşik)."""
    if not STRATEJI_KARNE:
        return None
    hedefler = {h for h, _ in STRATEJI_KARNE}
    en_yakin_hedef = min(hedefler, key=lambda h: abs(h - hedef))
    adaylar = [(e, v) for (h, e), v in STRATEJI_KARNE.items() if h == en_yakin_hedef]
    en_yakin_esik, deger = min(adaylar, key=lambda x: abs(x[0] - esik))
    return {**deger, "hedef": en_yakin_hedef, "esik": en_yakin_esik,
            "tam_eslesme": abs(en_yakin_hedef - hedef) < 0.01 and abs(en_yakin_esik - esik) < 0.01}

MIN_ORNEK = 500              # altında karne istatistiksel olarak gürültüdür
MAKS_SAPMA = 0.030           # tüm dağılımda kabul edilen en büyük kalibrasyon farkı
MIN_AYIRT = 0.050            # bunun altında model maça özel bilgi taşımıyor demektir
MAKS_KULLANIM_SAPMA = 0.035  # sistemin fiilen oynadığı (%60+) bölgede izin verilen sapma

# Arşivde OYUNCU verisi yok (football-data.co.uk maç bazlı: skor, korner, kart,
# şut). "X oyuncusu gol atar" pazarı bu yüzden fiyatlanamaz — tahmin üretmek
# uydurmak olurdu. Kadro/dakika/xG içeren ücretli bir kaynak gerekir.
FIYATLANAMAZ = {
    "oyuncu golü": "arşivde oyuncu verisi yok (maç bazlı veri: skor, korner, kart, şut)",
    "asist / kart göreni": "aynı sebep — oyuncu bazlı olay verisi yok",
    "ilk golü atan": "aynı sebep; ayrıca dakika verisi de yok",
}


def karne(pazar: str) -> dict | None:
    return PAZAR_KARNE.get(pazar)


def _kullanim(k: dict) -> dict | None:
    """Yalnız yeterince büyük örneklemli kullanım karnesi gösterilir.

    Bazı pazarların %60+ bölgesinde 1-3 maç var; oradaki "%100 tuttu" bir
    bilgi değil, gürültüdür — kullanıcıya öyle bir rozet gösterilmez.
    """
    ku = k.get("kullanim")
    return ku if ku and ku.get("n", 0) >= MIN_ORNEK else None


def guvenilir(pazar: str) -> tuple[bool, str]:
    """Pazar öneri havuzuna girebilir mi? (girer_mi, gerekçe)"""
    k = PAZAR_KARNE.get(pazar)
    if not k:
        return False, "ölçülmedi"
    if k["n"] < MIN_ORNEK:
        return False, f"örneklem küçük (n={k['n']})"
    if abs(k["fark"]) > MAKS_SAPMA:
        yon = "abartıyor" if k["fark"] < 0 else "eksik tahmin ediyor"
        return False, f"kalibrasyon bozuk: model {yon} ({k['fark']*100:+.1f} puan)"
    if k["ayirt"] < MIN_AYIRT:
        return False, f"ayırt gücü yok ({k['ayirt']*100:+.1f} puan)"
    ku = _kullanim(k)
    if ku and abs(ku["gercek"] - ku["dedi"]) > MAKS_KULLANIM_SAPMA:
        return False, (f"öneri bölgesinde bozuluyor "
                       f"({(ku['gercek']-ku['dedi'])*100:+.1f} puan)")
    return True, "ölçümü geçti"


def duzeltilmis(pazar: str, p: float) -> float:
    """Modelin olasılığı OLDUĞU GİBİ kullanılır — sapma düzeltmesi uygulanmaz.

    ÖLÇÜLDÜ VE REDDEDİLDİ: bir pazarda ölçülen sistematik sapmayı olasılığa
    geri eklemeyi sınadık. Test dönemi ikiye bölündü, düzeltme ilk yarıda
    öğrenilip ikinci yarıda uygulandı: 35 pazarda sapmayı azalttı, 26 pazarda
    ARTIRDI. Yazı-tura kadar güvenilir bir düzeltme, kullanıcıya gösterilen
    yüzdeye sahte hassasiyet katmaktan başka işe yaramaz. Bu yüzden düzeltme
    yok; onun yerine kalibrasyonu bozuk pazarlar havuza hiç alınmıyor
    (guvenilir() süzgeci). Fonksiyon çağrı yerlerini bozmamak için duruyor.
    """
    return max(0.01, min(0.99, float(p)))


def _karne_rozeti(k: dict | None) -> dict | None:
    """Bacak rozeti: öneri bölgesi karnesi varsa o, yoksa tüm dağılım."""
    if not k:
        return None
    ku = _kullanim(k)
    if ku:
        return {"n": ku["n"], "dedi": ku["dedi"], "gercek": ku["gercek"],
                "ayirt": k["ayirt"], "bolge": True, "guvenilir": True}
    return {"n": k["n"], "dedi": k["dedi"], "gercek": k["gercek"],
            "ayirt": k["ayirt"], "bolge": False, "guvenilir": True}


def _bacak(aday: dict) -> dict:
    """Havuz kaydını arayüzün beklediği bacak sözlüğüne çevirir."""
    k = PAZAR_KARNE.get(aday["pazar"])
    p = aday["p"]
    oran = aday.get("oran")
    return {
        "mac_id": aday.get("mac_id"),
        "ev_ad": aday["ev_ad"],
        "dep_ad": aday["dep_ad"],
        "saat": aday.get("saat", ""),
        "lig": aday.get("lig", ""),
        "pazar": aday["pazar"],
        "p": float(p),
        "adil": round(1.0 / max(p, 1e-6), 2),
        "oran": float(oran) if oran else None,
        "ev": (float(p) * float(oran) - 1.0) if oran else None,
        # Rozette gösterilen karne, sistemin fiilen oynadığı bölgeye ait olmalı:
        # MS1'in tüm dağılımdaki oranı %43.8 (ev sahibi kazanma taban oranı),
        # ama model %60+ dediğinde %71.5. Kullanıcıyı ilgilendiren ikincisi.
        "karne": _karne_rozeti(k),
        "katman": aday.get("katman", "temel"),
        "lig_derin": bool(aday.get("lig_derin")),
        "gerekce": aday.get("gerekce", []),
    }


# ─────────────────────────────────────────────────────────────────────────
# PAZARIN AÇILMA YAYGINLIĞI — ölçüm DEĞİL, kitapçı gözlemi
#
# Model bir pazarı doğru fiyatlayabiliyor olabilir ama kitapçı o maç için o
# pazarı AÇMAMIŞ olabilir; öneri o zaman oynanamaz. Kullanıcı bunu bildirdi:
# Peru 2. Lig ve Ekvador maçlarında yalnız 2.5 alt/üst açıkken sisteme
# ALT 3.5 / ALT 4.5 önerttik.
#
# Aşağıdaki katmanlar İSTATİSTİK DEĞİL, bahis sitelerinde hangi pazarın ne
# sıklıkla açıldığına dair gözlemdir; arşivde "kitapçı bu pazarı açtı mı"
# verisi yok, dolayısıyla ölçülemez. Bu yüzden ayrı tutuluyor ve arayüzde de
# ölçülmüş karneden ayrı gösteriliyor.
#   temel  : hemen her maçta açık (MS, çifte şans, KG, 2.5 alt/üst)
#   yaygin : orta ve büyük liglerde genelde açık (1.5/3.5 çizgileri, ilk yarı,
#            İY/MS, handikap, toplam korner)
#   genis  : çoğunlukla yalnız büyük lig/derbi kuponlarında (takım gol sayısı,
#            takım korneri, kart, 4.5+ çizgileri, iki yarı kombinasyonları)

TEMEL_PAZARLAR = {
    "MS1", "MS0", "MS2", "ÇŞ 1X", "ÇŞ 12", "ÇŞ X2",
    "KG VAR", "KG YOK", "ÜST 2.5", "ALT 2.5",
}

YAYGIN_PAZARLAR = TEMEL_PAZARLAR | {
    "ÜST 1.5", "ALT 1.5", "ÜST 3.5", "ALT 3.5",
    "İY 0.5 ÜST", "İY 0.5 ALT", "İY 1.5 ÜST", "İY 1.5 ALT",
    "İY 1", "İY 0", "İY 2", "İY KG VAR", "İY KG YOK",
    "2Y GOL VAR", "2Y GOL YOK",
    "HER İKİ YARI GOL VAR", "HER İKİ YARI GOL YOK",
    "HND 0:1 1", "HND 0:1 0", "HND 0:1 2",
    "HND 1:0 1", "HND 1:0 0", "HND 1:0 2",
    "1 ve ÜST 2.5", "1 ve ALT 2.5", "0 ve ÜST 2.5", "0 ve ALT 2.5",
    "2 ve ÜST 2.5", "2 ve ALT 2.5",
    "KORNER ÜST 8.5", "KORNER ALT 8.5", "KORNER ÜST 9.5", "KORNER ALT 9.5",
    "KORNER ÜST 10.5", "KORNER ALT 10.5",
}


def pazar_katmani(pazar: str) -> str:
    """Pazarın bahis sitesinde açılma yaygınlığı: temel / yaygin / genis."""
    if pazar in TEMEL_PAZARLAR:
        return "temel"
    if pazar in YAYGIN_PAZARLAR or pazar.startswith("İY/MS "):
        return "yaygin"
    return "genis"


# Derin pazar açılan ligler: büyük Avrupa ligleri, üst düzey kupalar ve
# yaygın takip edilen ulusal ligler. Buradaki maçlarda "genis" pazarlar da
# çoğunlukla açıktır. Liste eksikse zarar yok: eksik lig "sığ" sayılır ve
# sistem daha temkinli, oynanabilir pazarlar önerir.
DERIN_LIG_KODLARI = {
    # Avrupa'nın büyük ligleri ve alt ligleri
    "E0", "E1", "D1", "D2", "I1", "I2", "SP1", "SP2", "F1", "F2",
    "N1", "B1", "P1", "T1", "SC0", "G1",
    # Türkiye'de yoğun oynanan, kitapçının derin pazar açtığı ülke ligleri
    # (football-data.co.uk ek lig dosyalarının kodları)
    "BRA", "ARG", "MEX", "USA", "JPN", "RUS", "AUT", "DNK", "SWE", "NOR",
}
DERIN_LIG_IZLERI = (
    # ÜLKE ADIYLA NİTELENDİRİLDİ: çıplak "serie a" izi, "Ecuador Serie A"yı da
    # yakalayıp derin sayıyordu — kullanıcının şikâyet ettiği maç tam da oydu
    # (Orense–CSD Macara'ya ALT 4.5 önerilmişti, oysa kitapçı açmamış).
    # Yanlış "derin" demek oynanamaz öneri üretir; yanlış "sığ" demek yalnız
    # temkinli davranmaktır. Bu yüzden şüphede olduğumuzda sığ tarafta kalıyoruz.
    "england premier", "english premier", "championship",
    "spain laliga", "spain la liga", "italy serie a", "italian serie a",
    "germany bundesliga", "german bundesliga", "france ligue 1", "french ligue 1",
    "netherlands eredivisie", "portugal primeira", "scotland premiership",
    "turkey super", "turkey süper", "türkiye süper", "süper lig", "super lig",
    "belgium pro", "jupiler", "brazil serie a", "brasileirao", "brasileirão",
    "champions league", "şampiyonlar ligi", "europa league", "conference league",
    "major league soccer", "usa mls",
)


def lig_derinligi(lig: str | None, kod: str | None = None) -> str:
    """Bu ligde derin pazarlar açılır mı? 'derin' ya da 'sig'."""
    if kod and str(kod).strip() in DERIN_LIG_KODLARI:
        return "derin"
    ad = (lig or "").strip().lower()
    if not ad:
        return "sig"
    if ad in {k.lower() for k in DERIN_LIG_KODLARI}:
        return "derin"
    return "derin" if any(iz in ad for iz in DERIN_LIG_IZLERI) else "sig"


KAPSAM_SIRASI = {"temel": 0, "yaygin": 1, "genis": 2}


def kapsama_uygun(pazar: str, kapsam: str, lig_derin: bool) -> bool:
    """Pazar, seçilen kapsama ve ligin derinliğine göre önerilebilir mi?

    Merdiven:
      kapsam "temel"  → yalnız her maçta açık pazarlar
      kapsam "yaygin" → temel + yaygın; SIĞ ligde temele iner
      kapsam "genis"  → hepsi; SIĞ ligde yaygına iner
    Sığ ligde bir kademe inmenin sebebi ölçüm değil gözlem: küçük liglerde
    kitapçı derin pazarları açmıyor, açılmayan pazara öneri yapmak boş.
    """
    katman = KAPSAM_SIRASI[pazar_katmani(pazar)]
    tavan = KAPSAM_SIRASI.get(kapsam, 1)
    if not lig_derin:
        tavan = max(0, tavan - 1)
    return katman <= tavan


TABAN_ESIK = 0.40     # havuz tabanı; kupon eşiği bunun üstünde ayrıca uygulanır
TEKLI_MIN_P = 0.45    # ölçümde (bölüm E) bu tabanla %49.3 isabet / %+1.6 getiri çıktı


def havuz_kur(maclar: list[dict], esik: float = TABAN_ESIK,
              kapsam: str = "yaygin") -> tuple[list[dict], int, int]:
    """Maçlardan gelen seçim listelerini tek havuzda toplar, süzgeçlerden geçirir.

    İki ayrı süzgeç var ve karıştırılmamalı:
      1) ÖLÇÜM süzgeci — model bu pazarı doğru fiyatlıyor mu? (guvenilir)
      2) KAPSAM süzgeci — kitapçı bu pazarı bu maçta açıyor mu? (kapsama_uygun)
    İkincisi ölçüm değil gözlemdir; doğru fiyatlanan ama açılmayan bir pazar
    kullanıcı için işe yaramaz.

    maclar: [{"mac_id","ev_ad","dep_ad","saat","lig","lig_kodu","secenekler":[...]}]
    Döner: (havuz, olcum_elenen, kapsam_elenen)
    """
    havuz, elenen, kapsam_disi = [], 0, 0
    for m in maclar:
        derin = lig_derinligi(m.get("lig"), m.get("lig_kodu")) == "derin"
        for s in m.get("secenekler", []):
            if not kapsama_uygun(s["pazar"], kapsam, derin):
                kapsam_disi += 1
                continue
            gecer, _neden = guvenilir(s["pazar"])
            if not gecer:
                elenen += 1
                continue
            p = duzeltilmis(s["pazar"], float(s["p"]))
            if p < esik:
                continue
            k = PAZAR_KARNE[s["pazar"]]
            ku = _kullanim(k)
            gerekce = list(s.get("gerekce") or [])
            if ku:
                gerekce.append(
                    f"model bu pazarda %{ku['dedi']*100:.0f} dediği {ku['n']:,} maçta "
                    f"gerçekte %{ku['gercek']*100:.1f} tuttu".replace(",", "."))
            else:
                gerekce.append(
                    f"bu pazar {k['n']:,} maçta ölçüldü, sapması "
                    f"{k['fark']*100:+.1f} puan".replace(",", "."))
            gerekce.append(f"ayırt gücü {k['ayirt']*100:+.1f} puan")
            havuz.append({**m, **s, "p": p, "katman": pazar_katmani(s["pazar"]),
                          "lig_derin": derin, "gerekce": gerekce})
    # en güvenilirden başla; eşitlikte fiyatı olan (dolayısıyla EV'si ölçülebilen) önde
    havuz.sort(key=lambda x: (-x["p"], -(x.get("oran") or 0)))
    return havuz, elenen, kapsam_disi


def _fiyat(aday: dict) -> float:
    """Bacağın hesapta kullanılan oranı: gerçek fiyat varsa o, yoksa adil oran."""
    return float(aday.get("oran") or (1.0 / max(aday["p"], 1e-6)))


def kupon_kur(havuz: list[dict], hedef: float = 2.0, maks_bacak: int = 3,
              esik: float = 0.60) -> dict | None:
    """Hedef toplam orana ulaşan EN YÜKSEK OLASILIKLI kuponu arar.

    Neden açgözlü seçim yetmiyor: "en yüksek olasılıklıyı sırayla ekle" üç
    bacakta 1.64'te kalıp hedefi ıskalayabiliyor. Doğru soru "hedefi geçen
    kombinasyonlar içinde hangisinin tutma olasılığı en yüksek" — bu bir
    sırt çantası problemi, sıralamayla çözülmez. Aday listesi budanıp
    (kombinasyon sayısı kontrol altında) budamalı arama yapılır.

    Kural: her maçtan EN FAZLA bir bacak. Aynı maçtan iki seçim birbirine
    bağımlıdır (aynı skorun iki yüzü) — çarpım kuralı orada yalan söyler.
    Hedefe ulaşmak için asla eşik altı seçim eklenmez; ulaşılamıyorsa kupon
    kurulmaz ve bu açıkça söylenir.

    MATEMATİK NOTU: fiyatı bilinmeyen bacaklarda adil oran (1/p) kullanılır;
    öyle bacaklarla kurulan 2.00'lik kuponun olasılığı tam olarak %50 çıkar
    (çarpım birebir tersidir). Yani değer, ancak GERÇEK fiyatı adil oranın
    üstünde olan bacaklardan gelir.
    """
    adaylar = [a for a in havuz if a["p"] >= esik]
    if not adaylar:
        return None
    # verim = bir bacağın orana kattığı birim başına koruduğu olasılık;
    # adil fiyatlı bacakta tam olarak -1, değerli bacakta -1'den büyüktür.
    def verim(a: dict) -> float:
        o = _fiyat(a)
        return math.log(a["p"]) / math.log(o) if o > 1.0001 else -99.0

    adaylar.sort(key=verim, reverse=True)
    # kombinasyon patlamasını engelle: derinlik arttıkça aday listesi kısalır
    sinir = {1: 200, 2: 120, 3: 90, 4: 40, 5: 28, 6: 22}.get(maks_bacak, 30)
    adaylar = adaylar[:sinir]

    en_iyi: dict | None = None

    def dfs(basla: int, secili: list, maclar: set, oran: float, olasilik: float):
        nonlocal en_iyi
        if oran >= hedef and secili:
            if en_iyi is None or olasilik > en_iyi["p"]:
                en_iyi = {"bacaklar": list(secili), "oran": oran, "p": olasilik}
            return          # daha fazla bacak olasılığı yalnız düşürür
        if len(secili) >= maks_bacak:
            return
        for i in range(basla, len(adaylar)):
            a = adaylar[i]
            mid = a.get("mac_id")
            if mid is not None and mid in maclar:
                continue
            yeni_p = olasilik * a["p"]
            if en_iyi is not None and yeni_p <= en_iyi["p"]:
                continue    # buradan daha iyi bir sonuç çıkamaz (olasılık yalnız azalır)
            secili.append(a)
            if mid is not None:
                maclar.add(mid)
            dfs(i + 1, secili, maclar, oran * _fiyat(a), yeni_p)
            secili.pop()
            if mid is not None:
                maclar.discard(mid)

    dfs(0, [], set(), 1.0, 1.0)
    if not en_iyi:
        return None
    bacaklar, oran, p = en_iyi["bacaklar"], en_iyi["oran"], en_iyi["p"]
    fiyatsiz = sum(1 for b in bacaklar if not b.get("oran"))
    uyari = None
    if fiyatsiz == len(bacaklar):
        # Bu durumda %olasılık bir keşif değil, kimliktir: adil oran 1/p olduğu
        # için çarpımları hedefe eşitlendiğinde olasılık 1/hedef çıkar. Kullanıcı
        # bunu "sistem %50 hesapladı" sanmasın.
        uyari = (f"Bu kupondaki bacakların hiçbirinin gerçek fiyatı bültende yok; hepsi "
                 f"ADİL oranla (1/olasılık) hesaplandı. Bu yüzden tutma şansının "
                 f"%{1/oran*100:.0f} çıkması bir tahmin değil, matematiksel zorunluluk: "
                 f"adil oranlarla {oran:.2f}'ye ulaşan her kupon 1/{oran:.2f} olasılık verir. "
                 "Buradaki gerçek katkı, bacakların ÖLÇÜLMÜŞ karnesi yüksek pazarlardan "
                 "seçilmiş olması. Kâr edip etmeyeceğini ancak bahis sitesindeki gerçek "
                 "fiyatlar belirler — adil orandan yüksek fiyat bulursan kârdasın.")
    elif fiyatsiz:
        uyari = (f"{fiyatsiz} bacağın gerçek fiyatı elimizde yok (bültende o pazarın oranı "
                 "gelmiyor); toplam oran orada ADİL oranla hesaplandı. Bahis sitesindeki "
                 "fiyat adil oranın altındaysa kuponun gerçek getirisi burada yazandan "
                 "düşük olur — fiyatı sitede kontrol et.")
    return {
        "bacaklar": [_bacak(b) for b in bacaklar],
        "oran": round(oran, 2),
        "p": float(p),
        "ev": float(p * oran - 1.0),
        "basabas": float(1.0 / oran),
        "uyari": uyari,
    }


def tekli_degerler(havuz: list[dict], min_oran: float = 2.0, adet: int = 5) -> list[dict]:
    """Tek başına min_oran üstü, modele göre fiyatı cömert olan seçimler.

    Kupon eşiğinden bağımsız kendi tabanı vardır: gerçek fiyatı 2.00+ olan bir
    seçimin model olasılığı zaten %50 civarındadır, kupon eşiği (%60) burada
    her şeyi elerdi. Taban ölçümden geliyor (bölüm E: %45 tabanla 696 bahiste
    %49.3 isabet, %+1.6 getiri).
    """
    secilen = []
    for a in havuz:
        oran = a.get("oran")
        if not oran or oran < min_oran or a["p"] < TEKLI_MIN_P:
            continue
        if a["p"] * oran <= 1.0:      # değer yoksa öneri de yok
            continue
        secilen.append(a)
    secilen.sort(key=lambda x: -(x["p"] * x["oran"]))
    return [_bacak(a) for a in secilen[:adet]]


KARNE_NOT = ("137 pazarın hepsi 8.000 maçta ölçüldü (876.040 tekil ölçüm). Model yalnız "
             "01.07.2023 öncesi arşivle kuruldu, karne o tarihten sonraki maçlarda "
             "çıkarıldı — yani 'geleceği görerek' şişmiş sayılar değil. 'Gerçekte' "
             "sütunu, modelin %60+ dediği maçlara aittir (sistemin fiilen oynadığı "
             "bölge); yeterli örneklem yoksa tüm maçların ortalaması yazar. 'Ayırt "
             "gücü' modelin en güvendiği çeyrek ile en az güvendiği çeyrek arasındaki "
             "gerçek fark: sıfıra yakınsa model o pazarda maça özel bir şey bilmiyor "
             "demektir. Elenen pazarlar da listede — neyin denendiği görünsün diye.")


def _strateji_notu() -> str:
    """Bu stratejinin geçmişte gerçekte ne yaptığı — süslemesiz."""
    a = STRATEJI_KARNE[(2.0, 0.60)]
    kotu = STRATEJI_KARNE[(2.0, 0.70)]
    uc = STRATEJI_KARNE[(3.0, 0.55)]
    basabas = 1.0 / a["gercek"]
    return (
        "📊 <b>Bu sekmenin stratejisi geçmişte ne yaptı:</b> 305 günün bülteni "
        "üretimdeki kurallarla taranıp 1.812 kupon simüle edildi. Varsayılan ayarda "
        f"(2.00 hedef, %60 eşik) sistem <b>%{a['dedi']*100:.1f}</b> demişti, gerçekte "
        f"<b>%{a['gercek']*100:.1f}</b> tuttu ({a['n']} kupon) — yani söylediği olasılık "
        f"{abs(a['gercek']-a['dedi'])*100:.1f} puan hatayla doğru çıktı. "
        f"3.00 hedefte %{uc['dedi']*100:.1f} deyip %{uc['gercek']*100:.1f} tutmuş. "
        "<b>Bu kâr demek değildir:</b> bacakların çoğunun gerçek fiyatı bültende yok, "
        "o yüzden getiri ölçülemez. Ölçülebilen şey olasılığın dürüstlüğü — o da tamam. "
        f"Pratik kural: bu kuponu bahis sitende <b>{basabas:.2f}</b> ve üstü toplam "
        "orana kurabiliyorsan matematik senden yana, altındaysa değil."
        f"<br>⚠️ <b>Eşiği yükseltmek işe yaramıyor:</b> %70 eşikte isabet "
        f"%{kotu['gercek']*100:.1f}'e <b>düşüyor</b> ({kotu['n']} kupon) — eşik yükseldikçe "
        "havuz daralıyor ve kupon ölçümü zayıf uç pazarlara kayıyor. Önceki sürümde "
        "bunun tersini yazmıştık; geniş pazar havuzuyla ölçünce iddia çürüdü."
    )


def notlar(oransiz: int = 0, kapsam: str = "yaygin", sig_mac: int = 0) -> list[str]:
    """Sekmenin altındaki dürüstlük notları."""
    n = [
        "🎯 <b>'Garanti maç' diye bir şey yok</b> — bu sekme de onu vaat etmiyor. "
        "Matematik acımasız: 2.00 oran zaten 'iki denemeden biri tutar' demektir. "
        "Yüksek oran ile yüksek kesinlik aynı anda olmaz; burada yapılan, "
        "<b>aynı orana ulaşan en yüksek olasılıklı yolu</b> seçmektir.",
        "📋 Sıralama <b>ölçüme</b> dayanır, tahmine değil: 137 pazarın hepsi 8.000 maçlık "
        "eğitim/test ayrımından geçti (876.040 ölçüm). Kalibrasyonu bozuk, ayırt gücü sıfır "
        "ya da tam da öneri bölgesinde bozulan pazarlar <b>havuza hiç alınmadı</b> — "
        "tablodaki ⛔ satırları onlar, elenme sebepleriyle birlikte duruyorlar.",
        "🔍 Tablodaki 'gerçekte' sütunu, modelin <b>%60+ dediği</b> maçlara aittir; "
        "sistemin fiilen oynadığı bölge orası. Tüm maçların ortalaması yanıltır: "
        "MS1'in genel oranı %43.8'dir (ev sahibi kazanma taban oranı), ama model "
        "%60+ dediğinde %71.5 tutmuştur.",
        "🔗 Kombine bacakları hep <b>farklı maçlardan</b> seçilir. Aynı maçtan iki "
        "seçim birbirine bağımlıdır (aynı skorun iki yüzü); orada olasılıkları çarpmak "
        "kuponu olduğundan güvenli gösterir.",
        _kapsam_notu(kapsam, sig_mac),
        _strateji_notu(),
    ]
    eksik = " · ".join(f"<b>{ad}</b>: {sebep}" for ad, sebep in FIYATLANAMAZ.items())
    n.append("🚫 Fiyatlanamayan pazarlar (istense de eklenemez): " + eksik +
             ". Bu pazarlar için tahmin üretmek uydurmak olurdu; kadro, dakika ve "
             "xG içeren ücretli bir veri kaynağı gerekir.")
    if oransiz:
        n.append(f"ℹ️ Günün {oransiz} maçında oran bulunamadığı için o maçlar "
                 "değerlendirmeye girmedi.")
    return n


def _kapsam_notu(kapsam: str, sig_mac: int) -> str:
    """Açılmayan pazar sorununu ve bu süzgecin ölçüm OLMADIĞINI anlatır."""
    ad = {"temel": "Temel", "yaygin": "Yaygın", "genis": "Hepsi"}.get(kapsam, "Yaygın")
    return (
        f"🏷️ <b>Kitapçı bu pazarı açmış mı?</b> Model bir pazarı doğru fiyatlıyor olabilir "
        "ama bahis sitesi o maçta o pazarı açmamış olabilir; öyle bir öneri işe yaramaz. "
        f"Bu yüzden pazarlar açılma yaygınlığına göre üç katmana ayrıldı ve şu an "
        f"<b>{ad}</b> kapsamındasın. Küçük liglerde (2. lig, az takip edilen ülke ligleri) "
        "sistem bir kademe daha iniyor — orada kitapçı zaten yalnız temel pazarları açıyor"
        + (f"; bugün taranan maçların <b>{sig_mac} tanesi</b> bu gruba giriyor" if sig_mac else "")
        + ". <b>Dikkat:</b> bu katmanlar ölçüm değil <b>gözlemdir</b> — arşivde 'kitapçı bu "
        "pazarı açtı mı' verisi yok, dolayısıyla ölçülemez. Ölçülmüş karne ile "
        "karıştırılmasın diye ayrı tutuluyor. Bir öneri sitende yoksa kapsamı düşür."
    )


def karne_tablosu() -> list[dict]:
    """Arayüzdeki 'neyin ne kadar güvenilir olduğu' tablosu.

    Gösterilen 'dedi/gerçek', varsa öneri bölgesinin (model ≥%60) karnesidir;
    yoksa tüm dağılım. Elenenler de listede kalır — kullanıcı neyin denendiğini
    ve neden kullanılmadığını görebilsin.
    """
    satirlar = []
    for pazar, k in PAZAR_KARNE.items():
        gecer, neden = guvenilir(pazar)
        ku = _kullanim(k)
        gosterim = ku if ku else k
        satirlar.append({
            "pazar": pazar,
            "n": gosterim["n"],
            "dedi": gosterim["dedi"],
            "gercek": gosterim["gercek"],
            "fark": gosterim["gercek"] - gosterim["dedi"],
            "ayirt": k["ayirt"],
            "bolge": bool(ku),
            "guvenilir": gecer,
            "neden": None if gecer else neden,
        })
    satirlar.sort(key=lambda x: (not x["guvenilir"], -x["gercek"]))
    return satirlar


def fiyatlanamaz_satirlari() -> list[dict]:
    """Karne tablosunun sonuna eklenen 'hiç fiyatlanamayan' pazarlar.

    Kullanıcı "bütün marketleri koy" dediğinde, konulamayanların da ekranda
    görünmesi gerekir — yoksa unutulduğu sanılır.
    """
    return [{"pazar": ad, "n": 0, "dedi": None, "gercek": None, "fark": None,
             "ayirt": None, "bolge": False, "guvenilir": False, "neden": sebep,
             "veri_yok": True}
            for ad, sebep in FIYATLANAMAZ.items()]
