<?php
header('Access-Control-Allow-Origin: *');
header('Content-Type: image/png');
header('Access-Control-Expose-Headers: X-Last-Modified');

if (!isset($_GET['url'])) {
    http_response_code(400);
    die('Missing url parameter');
}

$url = $_GET['url'];

if (strpos($url, 'https://satda.tmd.go.th/') !== 0) {
    http_response_code(403);
    die('Forbidden: Target URL not allowed');
}

$context = stream_context_create([
    'http' => [
        'method' => 'GET',
        'header' => "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
    ],
    'ssl' => [
        'verify_peer' => false,
        'verify_peer_name' => false
    ]
]);

$image = file_get_contents($url, false, $context);

if ($image === false) {
    http_response_code(404);
    die('Image not found or upstream error');
}

// Extract Last-Modified header from the upstream response and pass it down
if (isset($http_response_header)) {
    foreach ($http_response_header as $header) {
        if (stripos($header, 'Last-Modified:') === 0) {
            header('X-' . $header);
            break;
        }
    }
}

echo $image;
?>
