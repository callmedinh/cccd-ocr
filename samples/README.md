# Ảnh mẫu — toàn bộ là dữ liệu hư cấu

Các ảnh trong thư mục này do `tools/make_samples.py` sinh ra để **kiểm thử** và
làm ví dụ minh hoạ:

- Không có thẻ thật nào ở đây. Họ tên, địa chỉ, số CCCD đều được bịa.
- Số CCCD vẫn tuân theo đúng cấu trúc thật (mã tỉnh + mã giới tính/thế kỷ +
  2 số cuối năm sinh) và MRZ có check digit hợp lệ, nên dùng để kiểm tra phần
  đối chiếu chéo là chính xác.
- Ảnh được vẽ lại từ đầu bằng Pillow, có thêm xoay nhẹ và nhiễu JPEG để giống
  ảnh chụp điện thoại.

Muốn tạo lại:

```powershell
python tools/make_samples.py
```

Dùng để thử nhanh:

```powershell
python main.py samples/ -o output/cccd.xlsx --csv
```
