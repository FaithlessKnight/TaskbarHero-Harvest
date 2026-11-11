$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType=WindowsRuntime]
$awaitMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Wait-WinRT($Operation, [Type]$ResultType) {
    $task = $awaitMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}
try {
    $language = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
        Where-Object { $_.LanguageTag -like 'zh-Hans*' } | Select-Object -First 1
    if (!$language) { throw 'Windows Chinese OCR is unavailable.' }
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    $englishLanguage = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
        Where-Object { $_.LanguageTag -like 'en-*' } | Select-Object -First 1
    $englishEngine = $null
    if ($englishLanguage) { $englishEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($englishLanguage) }
    @{ ready = $true; language = $language.LanguageTag; maxSize = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension } |
        ConvertTo-Json -Compress | ForEach-Object { [Console]::WriteLine($_) }
    while ($null -ne ($line = [Console]::ReadLine())) {
        $stream = $null
        $bitmap = $null
        try {
            $request = $line | ConvertFrom-Json
            $file = Wait-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($request.path)) ([Windows.Storage.StorageFile])
            $stream = Wait-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
            $decoder = Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
            $scale = 1
            if ($request.scale) { $scale = [Math]::Min(5, [Math]::Max(1, [int]$request.scale)) }
            $transform.ScaledWidth = $decoder.PixelWidth * $scale
            $transform.ScaledHeight = $decoder.PixelHeight * $scale
            $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::NearestNeighbor
            if ($request.interpolation -eq 'cubic') {
                $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Cubic
            }
            $bitmap = Wait-WinRT ($decoder.GetSoftwareBitmapAsync(
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore,
                $transform,
                [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
                [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $selectedEngine = $engine
            if ($request.language -eq 'en' -and $englishEngine) { $selectedEngine = $englishEngine }
            $result = Wait-WinRT ($selectedEngine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $lines = @($result.Lines | ForEach-Object { $_.Text })
            $details = @($result.Lines | ForEach-Object {
                $words = @($_.Words | ForEach-Object {
                    $b = $_.BoundingRect
                    @{ text = $_.Text; box = @(($b.X / $scale), ($b.Y / $scale), ($b.Width / $scale), ($b.Height / $scale)) }
                })
                @{ text = $_.Text; words = $words }
            })
            @{ ok = $true; text = $result.Text; lines = $lines; details = $details } | ConvertTo-Json -Compress -Depth 8 |
                ForEach-Object { [Console]::WriteLine($_) }
        } catch {
            @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress |
                ForEach-Object { [Console]::WriteLine($_) }
        } finally {
            if ($bitmap) { $bitmap.Dispose() }
            if ($stream) { $stream.Dispose() }
        }
    }
} catch {
    @{ ready = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress |
        ForEach-Object { [Console]::WriteLine($_) }
    exit 1
}
