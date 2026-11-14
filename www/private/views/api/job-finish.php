<?php
include('config.php');
require_once('helper.php');

// POST /api/jobs/<id>/finish: convert a job that waits for rear pages now,
// without them. POST /api/scanner/finish: the same for the newest waiting job.
// The scan scripts own the job's working directory, so the request runs
// script/finishjob.py as the scanner user, like the scan triggers.

if (isset($job_id)) {
        if (!job_id_is_valid($job_id)) {
                send_json_error(400, 'Invalid job id (expected YYYY-MM-DD-HH-MM-SS)');
        }
        $record = job_record($job_id);
        if ($record === null) {
                send_json_error(404, 'Unknown job');
        }
        if (($record['state'] ?? '') !== 'waiting_for_rear') {
                send_json_error(409, 'Job is not waiting for rear pages (state: ' . ($record['state'] ?? 'unknown') . ')');
        }
} else {
        $job_id = null;
        foreach (job_list(50) as $job) {
                if (($job['state'] ?? '') === 'waiting_for_rear') {
                        $job_id = $job['job'];
                        break;
                }
        }
        if ($job_id === null || !job_id_is_valid($job_id)) {
                send_json_error(409, 'No job is waiting for rear pages');
        }
}

$client = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
trigger_error("finish without rear pages for job $job_id requested via API from $client", E_API);
$command = 'sudo -u \#' . (int)$UID . ' /opt/brother/scanner/brscan-skey/script/finishjob.py --job-id ' . escapeshellarg($job_id);
$output = array();
$status = 1;
exec($command . ' 2>&1', $output, $status);
if ($status !== 0) {
        send_json_error(409, 'Job is not waiting for rear pages any more');
}

json(array(
        'message' => 'Job finishes without rear pages',
        'job' => $job_id,
        'status_url' => '/api/jobs/' . $job_id,
));
?>
