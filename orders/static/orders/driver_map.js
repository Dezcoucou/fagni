/* FAGNI - Driver Map (Lot 2)
   - Google Maps JS API
   - Markers: pickup, laundry, delivery
   - Directions: pickup -> laundry -> delivery (when possible)
*/

(function () {
  function toFloat(v) {
    if (v === null || v === undefined) return null;
    const s = String(v).trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function hasLatLng(lat, lng) {
    return typeof lat === "number" && typeof lng === "number";
  }

  function setStatus(el, msg) {
    const status = el.querySelector(".js-map-status");
    if (status) status.textContent = msg || "";
  }

  function buildPoint(label, lat, lng, address) {
    return { label: label, lat: lat, lng: lng, address: address || "" };
  }

  function defaultCenter() {
    // Abidjan (centre approx)
    return { lat: 5.348, lng: -4.027 };
  }

  function addMarker(map, point, title, iconColor) {
    const marker = new google.maps.Marker({
      map,
      position: { lat: point.lat, lng: point.lng },
      title: title,
      label: point.label,
    });
    return marker;
  }

  function fitAll(map, points) {
    const bounds = new google.maps.LatLngBounds();
    points.forEach((p) => bounds.extend({ lat: p.lat, lng: p.lng }));
    map.fitBounds(bounds);
  }

  async function geocodeAddress(geocoder, address) {
    return new Promise((resolve) => {
      if (!address) return resolve(null);
      geocoder.geocode({ address: address }, (results, status) => {
        if (status === "OK" && results && results[0] && results[0].geometry) {
          const loc = results[0].geometry.location;
          resolve({ lat: loc.lat(), lng: loc.lng() });
        } else {
          resolve(null);
        }
      });
    });
  }

  async function ensureCoords(geocoder, point) {
    if (hasLatLng(point.lat, point.lng)) return point;
    if (!point.address) return point;

    const r = await geocodeAddress(geocoder, point.address);
    if (r) {
      point.lat = r.lat;
      point.lng = r.lng;
    }
    return point;
  }

  function drawRoute(map, points, statusEl) {
    // Need at least pickup + delivery; laundry is optional waypoint
    const pickup = points.find((p) => p.label === "C");
    const laundry = points.find((p) => p.label === "B");
    const delivery = points.find((p) => p.label === "L");

    if (!pickup || !delivery || !hasLatLng(pickup.lat, pickup.lng) || !hasLatLng(delivery.lat, delivery.lng)) {
      if (statusEl) setStatus(statusEl, "Trajet indisponible (coordonnées insuffisantes).");
      return;
    }

    const directionsService = new google.maps.DirectionsService();
    const directionsRenderer = new google.maps.DirectionsRenderer({
      map,
      suppressMarkers: true,
      preserveViewport: false,
    });

    const waypoints = [];
    if (laundry && hasLatLng(laundry.lat, laundry.lng)) {
      waypoints.push({ location: { lat: laundry.lat, lng: laundry.lng }, stopover: true });
    }

    directionsService.route(
      {
        origin: { lat: pickup.lat, lng: pickup.lng },
        destination: { lat: delivery.lat, lng: delivery.lng },
        waypoints: waypoints,
        travelMode: google.maps.TravelMode.DRIVING,
      },
      (result, status) => {
        if (status === "OK") {
          directionsRenderer.setDirections(result);
          if (statusEl) setStatus(statusEl, "Trajet calculé ✅");
        } else {
          if (statusEl) setStatus(statusEl, "Trajet non calculé (Directions indisponible).");
        }
      }
    );
  }

  window.initFagniDriverMap = async function initFagniDriverMap() {
    const host = document.getElementById("fagniDriverMap");
    if (!host) return;

    try {
      setStatus(host, "Chargement de la carte…");

      const pickupLat = toFloat(host.dataset.pickupLat);
      const pickupLng = toFloat(host.dataset.pickupLng);
      const pickupAddr = (host.dataset.pickupAddr || "").trim();

      const laundLat = toFloat(host.dataset.laundryLat);
      const laundLng = toFloat(host.dataset.laundryLng);
      const laundAddr = (host.dataset.laundryAddr || "").trim();

      const delLat = toFloat(host.dataset.deliveryLat);
      const delLng = toFloat(host.dataset.deliveryLng);
      const delAddr = (host.dataset.deliveryAddr || "").trim();

      const pickup = buildPoint("C", pickupLat, pickupLng, pickupAddr);      // C = Collecte
      const laundry = buildPoint("B", laundLat, laundLng, laundAddr);        // B = Blanchisserie
      const delivery = buildPoint("L", delLat, delLng, delAddr);             // L = Livraison

      const map = new google.maps.Map(host.querySelector(".js-map-canvas"), {
        center: defaultCenter(),
        zoom: 12,
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: true,
      });

      const geocoder = new google.maps.Geocoder();

      // Try to geocode missing coords (addresses)
      await ensureCoords(geocoder, pickup);
      await ensureCoords(geocoder, laundry);
      await ensureCoords(geocoder, delivery);

      const points = [pickup, laundry, delivery].filter((p) => hasLatLng(p.lat, p.lng));

      if (points.length === 0) {
        setStatus(host, "Aucune coordonnée exploitable (ni GPS ni adresse).");
        return;
      }

      // Markers
      if (hasLatLng(pickup.lat, pickup.lng)) addMarker(map, pickup, "Collecte (client)", null);
      if (hasLatLng(laundry.lat, laundry.lng)) addMarker(map, laundry, "Blanchisserie", null);
      if (hasLatLng(delivery.lat, delivery.lng)) addMarker(map, delivery, "Livraison (client)", null);

      // Fit bounds
      fitAll(map, points);

      // Route
      drawRoute(map, [pickup, laundry, delivery], host);
    } catch (e) {
      setStatus(host, "Erreur carte (JS).");
      // eslint-disable-next-line no-console
      console.error(e);
    }
  };
})();
