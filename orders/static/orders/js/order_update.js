let map, marker, geocoder, autocomplete;

function initMap() {
    const mapElement = document.getElementById('map');
    if (!mapElement || !window.google || !google.maps) {
        console.log("Google Maps non disponible pour le moment.");
        return;
    }

    const latField = document.getElementById('client_lat');
    const lngField = document.getElementById('client_lng');
    const addressInput = document.getElementById('client_address');

    let latVal = parseFloat(latField && latField.value ? latField.value : '0');
    let lngVal = parseFloat(lngField && lngField.value ? lngField.value : '0');

    let center;
    if (!isNaN(latVal) && !isNaN(lngVal) && latVal !== 0 && lngVal !== 0) {
        center = {lat: latVal, lng: lngVal};
    } else {
        center = {lat: 5.347, lng: -4.026}; // Abidjan
    }

    map = new google.maps.Map(mapElement, {
        zoom: 12,
        center: center,
    });

    geocoder = new google.maps.Geocoder();
    marker = new google.maps.Marker({
        map: map,
        position: center,
    });

    // Autocomplétion d'adresse Google Places
    if (addressInput && google.maps.places) {
        autocomplete = new google.maps.places.Autocomplete(addressInput, {
            componentRestrictions: { country: "ci" },
            fields: ["geometry", "formatted_address"]
        });

        autocomplete.addListener("place_changed", function () {
            const place = autocomplete.getPlace();
            if (!place.geometry || !place.geometry.location) return;

            const loc = place.geometry.location;
            map.setCenter(loc);
            map.setZoom(14);
            marker.setPosition(loc);

            if (latField && lngField) {
                latField.value = loc.lat();
                lngField.value = loc.lng();
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', function () {
    const phoneInput   = document.getElementById('client_phone');
    const nameInput    = document.getElementById('client_name');
    const addressInput = document.getElementById('client_address');
    const latInput     = document.getElementById('client_lat');
    const lngInput     = document.getElementById('client_lng');
    const locateBtn    = document.getElementById('locateBtn');
    const form         = document.getElementById('orderForm');
    const previewBtn   = document.getElementById('previewBtn');
    const summaryBox   = document.getElementById('summaryBox');
    const summaryContent = document.getElementById('summaryContent');
    const itemsTableBody = document.querySelector('#itemsTableBody, #itemsTable tbody');

    // 1) Autocomplétion client par téléphone (même logique que create.html)
    if (phoneInput && nameInput && addressInput) {
        const lookupUrl = phoneInput.getAttribute('data-lookup-url') || '/orders/api/client/';

        phoneInput.addEventListener('input', function () {
            const phone = phoneInput.value.trim();
            if (phone.length < 4) return;

            const url = lookupUrl + (lookupUrl.includes('?') ? '&' : '?') + 'phone=' + encodeURIComponent(phone);
            console.log('[CLIENT LOOKUP] appel API :', url);

            fetch(url)
                .then(res => {
                    console.log('[CLIENT LOOKUP] status :', res.status);
                    if (!res.ok) return null;
                    return res.json();
                })
                .then(data => {
                    console.log('[CLIENT LOOKUP] payload :', data);
                    if (!data) return;

                    let existsFlag = data.exists;
                    let c = data.customer || data;

                    if (existsFlag === false) {
                        return;
                    }

                    const nameVal =
                        c.name ||
                        c.full_name ||
                        c.customer_name ||
                        '';

                    const addressVal =
                        c.address ||
                        c.customer_address ||
                        c.location ||
                        '';

                    const latVal = c.latitude || c.lat || null;
                    const lngVal = c.longitude || c.lng || null;

                    if (nameVal && !nameInput.value.trim()) {
                        nameInput.value = nameVal;
                    }
                    if (addressVal && !addressInput.value.trim()) {
                        addressInput.value = addressVal;
                    }
                    if (latInput && latVal) {
                        latInput.value = latVal;
                    }
                    if (lngInput && lngVal) {
                        lngInput.value = lngVal;
                    }
                })
                .catch(err => console.error('Erreur API client :', err));
        });
    }

    // 2) Bouton "📍 Me localiser" (GPS + reverse geocoding)
    if (locateBtn && navigator.geolocation) {
        locateBtn.addEventListener('click', function () {
            locateBtn.disabled = true;
            locateBtn.textContent = "📍 Localisation...";

            navigator.geolocation.getCurrentPosition(
                function (pos) {
                    const lat = pos.coords.latitude;
                    const lng = pos.coords.longitude;

                    if (latInput && lngInput) {
                        latInput.value = lat;
                        lngInput.value = lng;
                    }

                    if (map && marker) {
                        const loc = {lat: lat, lng: lng};
                        map.setCenter(loc);
                        map.setZoom(14);
                        marker.setPosition(loc);
                    }

                    // Reverse geocoding → remplir l’adresse
                    if (geocoder) {
                        geocoder.geocode({ location: { lat: lat, lng: lng } }, function(results, status) {
                            if (status === 'OK' && results && results[0]) {
                                if (addressInput) {
                                    addressInput.value = results[0].formatted_address;
                                }
                            } else {
                                console.warn('Reverse geocode échoué :', status);
                            }
                        });
                    }

                    locateBtn.disabled = false;
                    locateBtn.textContent = "📍 Me localiser";
                },
                function (err) {
                    console.error("Erreur géolocalisation :", err);
                    alert("Impossible de récupérer votre position.");
                    locateBtn.disabled = false;
                    locateBtn.textContent = "📍 Me localiser";
                },
                {
                    enableHighAccuracy: true,
                    timeout: 10000,
                    maximumAge: 0,
                }
            );
        });
    }

    // 3) Bouton Photos : ouvre le <input type="file">
    document.body.addEventListener('click', function (e) {
        if (e.target.classList.contains('btn-photo')) {
            const row = e.target.closest('tr');
            const fileInput = row ? row.querySelector('input[type="file"]') : null;
            if (fileInput) {
                fileInput.click();
            }
        }
    });

    // 4) Résumé (si présent dans le template)
    if (previewBtn && summaryBox && summaryContent && itemsTableBody) {
        previewBtn.addEventListener('click', function (e) {
            e.preventDefault();
            const rows = itemsTableBody.querySelectorAll('tr');
            let html = '';
            rows.forEach(row => {
                const tds = row.querySelectorAll('td');
                if (tds.length < 4) return;
                const labelEl = tds[0].querySelector('.line-designation');
                const designation = labelEl
                    ? labelEl.textContent.trim()
                    : tds[0].textContent.trim();
                const qty = tds[1].textContent.trim();
                const pu = tds[2].textContent.trim();
                const total = tds[3].textContent.trim();
                html += `<p class="summary-item"><strong>${qty}</strong> × ${designation} — ${pu} (=${total})</p>`;
            });
            if (!html) {
                html = '<p class="summary-item">Aucune ligne dans la commande.</p>';
            }
            summaryContent.innerHTML = html;
            summaryBox.classList.remove('hidden');
        });
    }

    // 5) Validation basique avant submit
    if (form && itemsTableBody) {
        form.addEventListener('submit', function (e) {
            const rows = itemsTableBody.querySelectorAll('tr');
            if (!phoneInput.value.trim() || !nameInput.value.trim()) {
                e.preventDefault();
                alert("Veuillez renseigner au moins le nom et le téléphone du client.");
                return;
            }
            if (!rows.length) {
                e.preventDefault();
                alert("Ajoute au moins une ligne de prestation.");
                return;
            }
        });
    }
});
