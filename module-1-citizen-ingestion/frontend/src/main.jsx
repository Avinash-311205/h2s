import React, { useState } from 'react';
import ReactDOM from 'react-dom/client';

function App() {
  const [text, setText] = useState('');
  const [locationText, setLocationText] = useState('');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [image, setImage] = useState(null);
  const [audio, setAudio] = useState(null);
  const [imageStatus, setImageStatus] = useState('No image selected');
  const [audioStatus, setAudioStatus] = useState('No audio selected');
  const [channel, setChannel] = useState('web');
  const [requestId, setRequestId] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [requestJson, setRequestJson] = useState('');

  function getPayload() {
    return {
      channel,
      text: text.trim() || undefined,
      location: locationText.trim() || undefined,
      latitude: latitude !== '' ? Number(latitude) : undefined,
      longitude: longitude !== '' ? Number(longitude) : undefined,
    };
  }

  function updateJsonPreview() {
    setRequestJson(JSON.stringify(getPayload(), null, 2));
  }

  async function getUserLocation() {
    if (!navigator.geolocation) {
      setError('Geolocation is not supported in this browser. Please enter coordinates manually.');
      return;
    }

    setError('');

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const lat = position.coords.latitude.toString();
        const lng = position.coords.longitude.toString();
        setLatitude(lat);
        setLongitude(lng);
        setLocationText('Current location');
        updateJsonPreview();
      },
      () => {
        setError('Unable to fetch your location. Please enter the coordinates or place manually.');
      }
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setSuccess(false);

    const payload = getPayload();
    setRequestJson(JSON.stringify(payload, null, 2));

    if (!payload.text && !payload.location && (payload.latitude === undefined || payload.longitude === undefined)) {
      setError('Please add text, a location name, or coordinates before submitting.');
      return;
    }

    if (payload.latitude !== undefined && (payload.latitude < -90 || payload.latitude > 90)) {
      setError('Latitude must be between -90 and 90.');
      return;
    }

    if (payload.longitude !== undefined && (payload.longitude < -180 || payload.longitude > 180)) {
      setError('Longitude must be between -180 and 180.');
      return;
    }

    try {
      const response = await fetch('http://localhost:8000/api/v1/requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || 'Failed to submit request');
      }

      const id = result.request_id;
      setRequestId(id);

      if (image) {
        setImageStatus('Uploading image...');
        const formData = new FormData();
        formData.append('file', image);
        const imageResponse = await fetch(`http://localhost:8000/api/v1/requests/${id}/media`, {
          method: 'POST',
          body: formData,
        });

        if (!imageResponse.ok) {
          throw new Error('Image upload failed.');
        }

        setImageStatus(`Uploaded: ${image.name}`);
      } else {
        setImageStatus('No image selected');
      }

      if (audio) {
        setAudioStatus('Uploading audio...');
        const formData = new FormData();
        formData.append('file', audio);
        const audioResponse = await fetch(`http://localhost:8000/api/v1/requests/${id}/media`, {
          method: 'POST',
          body: formData,
        });

        if (!audioResponse.ok) {
          throw new Error('Audio upload failed.');
        }

        setAudioStatus(`Uploaded: ${audio.name}`);
      } else {
        setAudioStatus('No audio selected');
      }

      setSuccess(true);
      setText('');
      setLocationText('');
      setLatitude('');
      setLongitude('');
      setImage(null);
      setAudio(null);
      setRequestJson(JSON.stringify({ ...payload, request_id: id, status: 'RECEIVED' }, null, 2));
    } catch (err) {
      setError(err.message || 'Something went wrong while submitting the request.');
    }
  }

  return (
    <div className="container">
      <h1>NITI-SETU</h1>
      <p style={{ textAlign: 'center', marginTop: '-8px' }}>Citizen Development Request</p>

      {success ? (
        <div className="success">
          <strong>Request submitted successfully.</strong>
          <p style={{ margin: '12px 0 0' }}>Request ID:<br />{requestId}</p>
          <pre style={{ marginTop: '12px', background: '#f8fafc', padding: '12px', borderRadius: '12px', overflowX: 'auto' }}>{requestJson}</pre>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          <div>
            <label htmlFor="text">Describe your issue:</label>
            <textarea id="text" rows="6" value={text} onChange={(e) => { setText(e.target.value); updateJsonPreview(); }} placeholder="Example: The road near the school is flooded and broken." />
          </div>

          <div className="file-row">
            <div>
              <label>
                <input type="file" accept="image/*" onChange={(e) => { setImage(e.target.files?.[0] || null); setImageStatus(e.target.files?.[0] ? `Selected: ${e.target.files[0].name}` : 'No image selected'); updateJsonPreview(); }} hidden />
                Upload Photo
              </label>
              <div className="status-text">{imageStatus}</div>
            </div>
            <div>
              <label>
                <input type="file" accept="audio/*" onChange={(e) => { setAudio(e.target.files?.[0] || null); setAudioStatus(e.target.files?.[0] ? `Selected: ${e.target.files[0].name}` : 'No audio selected'); updateJsonPreview(); }} hidden />
                Upload Audio
              </label>
              <div className="status-text">{audioStatus}</div>
            </div>
          </div>

          <div>
            <label htmlFor="channel">Submission channel</label>
            <select id="channel" value={channel} onChange={(e) => { setChannel(e.target.value); updateJsonPreview(); }} style={{ width: '100%', padding: '12px 14px', borderRadius: '10px', border: '1px solid #d1d5db' }}>
              <option value="web">Web</option>
              <option value="mobile">Mobile</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="telegram">Telegram</option>
            </select>
          </div>

          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <label>Location</label>
              <button type="button" onClick={getUserLocation} style={{ border: 'none', background: 'transparent', color: '#2563eb', cursor: 'pointer' }}>Use my location</button>
            </div>
            <div style={{ marginTop: '8px' }}>
              <input type="text" value={locationText} onChange={(e) => { setLocationText(e.target.value); updateJsonPreview(); }} placeholder="Enter a place name e.g. Near City Hospital, Bengaluru" />
            </div>
            <div className="row" style={{ marginTop: '12px' }}>
              <label>
                <span className="muted">Latitude:</span>
                <input type="number" step="any" value={latitude} onChange={(e) => { setLatitude(e.target.value); updateJsonPreview(); }} placeholder="17.3850" />
              </label>
              <label>
                <span className="muted">Longitude:</span>
                <input type="number" step="any" value={longitude} onChange={(e) => { setLongitude(e.target.value); updateJsonPreview(); }} placeholder="78.4867" />
              </label>
            </div>
          </div>

          <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '12px', padding: '12px' }}>
            <strong>JSON payload preview</strong>
            <pre style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{requestJson || JSON.stringify(getPayload(), null, 2)}</pre>
          </div>

          {error && <div style={{ color: '#b91c1c', fontWeight: 600 }}>{error}</div>}

          <button type="submit" className="button">Submit Request</button>
        </form>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
