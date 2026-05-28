# M3U Logo Optimizer Test

Bu repo, Logo Manager ana sistemini bozmadan büyük M3U listelerinde `tvg-logo` alanlarını test amaçlı optimize etmek için hazırlanmıştır.

## Amaç

- M3U içindeki benzersiz `tvg-logo` URL'lerini tespit etmek
- Benzersiz logoları/posterleri `liste-logo/` klasörüne indirmek
- `liste-logolar.json` üretmek
- M3U başlığına `x-logo-source="liste-logolar.json"` eklemek
- `#EXTINF` satırlarından `tvg-logo` alanını silmek
- Kanal/dizi/film başlığına göre `logo-id="..."` eklemek
- Optimize edilmiş listeyi `output/optimized.m3u` olarak üretmek

## GitHub üzerinden kullanım

1. `index.html` sayfasını GitHub Pages veya direkt tarayıcıdan aç.
2. GitHub token, owner, repo ve branch bilgilerini gir.
3. M3U dosyasını panelden yükle.
4. Panel workflow'u başlatır.
5. GitHub Actions işlemi bitince `output/optimized.m3u`, `output/report.json` ve `liste-logolar.json` oluşur.

## Yerel test

```bash
python optimizer.py input/listem.m3u
```

Logo indirmeyi kapatmak için:

```bash
python optimizer.py input/listem.m3u --no-download
```

İlk testlerde indirme sayısını sınırlamak için:

```bash
python optimizer.py input/listem.m3u --max-downloads 100
```

## Üretilen M3U örneği

```m3u
#EXTM3U x-logo-source="liste-logolar.json"
#EXTINF:-1 logo-id="VIASAT_HISTORY" group-title="Belgesel" tvg-name="VIASAT HISTORY",VIASAT HISTORY
http://example.com/stream.m3u8
```

## Not

Bu repo deney alanıdır. Ana Logo Manager reposundaki `index.html`, `logos/` ve `logolar.json` dosyalarına dokunmaz.
