/**
 * FAGNI Crowd - Frontend JavaScript
 */

// Token FCM (à récupérer depuis le cookie ou localStorage)
function getCrowdToken() {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        const [name, value] = cookie.trim().split('=');
        if (name === 'crowd_token') {
            return value;
        }
    }
    return null;
}

// Headers pour les appels API
function getApiHeaders() {
    const token = getCrowdToken();
    return {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
    };
}

// Charger les offres disponibles
async function loadOffers() {
    const container = document.getElementById('offers-container');
    if (!container) return;
    
    try {
        const response = await fetch('/api/crowd/offers/', {
            headers: getApiHeaders(),
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.count === 0) {
            container.innerHTML = `
                <div class="alert alert-info text-center">
                    <i class="bi bi-info-circle"></i> Aucune offre disponible pour le moment
                </div>
            `;
            return;
        }
        
        let html = '<div class="row g-3">';
        data.offers.forEach(offer => {
            const timeLeft = offer.expires_at ? getTimeLeft(offer.expires_at) : 'N/A';
            html += `
                <div class="col-md-6 col-lg-4">
                    <div class="card offer-card h-100">
                        <div class="card-body">
                            <div class="d-flex justify-content-between align-items-start mb-2">
                                <h5 class="card-title mb-0">
                                    <i class="bi bi-box-seam"></i> ${offer.order_code}
                                </h5>
                                <span class="badge bg-primary badge-time">${timeLeft}</span>
                            </div>
                            <p class="card-text text-muted small mb-2">
                                <i class="bi bi-geo-alt"></i> ${offer.pickup_address || 'Adresse non renseignée'}
                            </p>
                            <p class="card-text small mb-3">
                                <strong>Type:</strong> ${offer.leg_type === 'pickup' ? 'Collecte' : 'Retour'}<br>
                                <strong>Montant:</strong> ${offer.amount} FCFA
                            </p>
                            <div class="d-grid gap-2">
                                <button class="btn btn-success btn-action" onclick="acceptOffer(${offer.offer_id})">
                                    <i class="bi bi-check-circle"></i> Accepter
                                </button>
                                <button class="btn btn-outline-danger btn-action" onclick="rejectOffer(${offer.offer_id})">
                                    <i class="bi bi-x-circle"></i> Refuser
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        
        container.innerHTML = html;
        
    } catch (error) {
        console.error('Erreur chargement offres:', error);
        container.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle"></i> Erreur de chargement des offres
            </div>
        `;
    }
}

// Accepter une offre
async function acceptOffer(offerId) {
    if (!confirm('Confirmer l\'acceptation de cette offre ?')) return;
    
    try {
        const response = await fetch(`/api/crowd/offers/${offerId}/accept/`, {
            method: 'POST',
            headers: getApiHeaders(),
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || `HTTP ${response.status}`);
        }
        
        alert('✅ Offre acceptée avec succès !');
        loadOffers(); // Recharger la liste
        
    } catch (error) {
        console.error('Erreur acceptation:', error);
        alert(`❌ Erreur: ${error.message}`);
    }
}

// Rejeter une offre
async function rejectOffer(offerId) {
    if (!confirm('Confirmer le rejet de cette offre ?')) return;
    
    try {
        const response = await fetch(`/api/crowd/offers/${offerId}/reject/`, {
            method: 'POST',
            headers: getApiHeaders(),
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        alert('Offre rejetée');
        loadOffers(); // Recharger la liste
        
    } catch (error) {
        console.error('Erreur rejet:', error);
        alert(`❌ Erreur: ${error.message}`);
    }
}

// Charger les missions acceptées
async function loadMissions() {
    const container = document.getElementById('missions-container');
    if (!container) return;
    
    try {
        const response = await fetch('/api/crowd/missions/', {
            headers: getApiHeaders(),
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.count === 0) {
            container.innerHTML = `
                <div class="alert alert-info text-center">
                    <i class="bi bi-info-circle"></i> Aucune mission en cours
                </div>
            `;
            return;
        }
        
        let html = '<div class="row g-3">';
        data.missions.forEach(mission => {
            const statusBadge = getStatusBadge(mission.status);
            html += `
                <div class="col-md-6 col-lg-4">
                    <div class="card mission-card h-100">
                        <div class="card-body">
                            <div class="d-flex justify-content-between align-items-start mb-2">
                                <h5 class="card-title mb-0">
                                    <i class="bi bi-briefcase"></i> ${mission.order_code}
                                </h5>
                                ${statusBadge}
                            </div>
                            <p class="card-text text-muted small mb-2">
                                <i class="bi bi-geo-alt"></i> ${mission.pickup_address || 'Adresse non renseignée'}
                            </p>
                            <p class="card-text small">
                                <strong>Type:</strong> ${mission.leg_type === 'pickup' ? 'Collecte' : 'Retour'}<br>
                                <strong>Montant:</strong> ${mission.amount} FCFA<br>
                                <strong>Acceptée:</strong> ${formatDate(mission.accepted_at)}
                            </p>
                        </div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        
        container.innerHTML = html;
        
    } catch (error) {
        console.error('Erreur chargement missions:', error);
        container.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle"></i> Erreur de chargement des missions
            </div>
        `;
    }
}

// Utilitaires
function getTimeLeft(expiresAt) {
    const now = new Date();
    const expires = new Date(expiresAt);
    const diff = Math.floor((expires - now) / 1000); // secondes
    
    if (diff <= 0) return 'Expirée';
    
    const minutes = Math.floor(diff / 60);
    const seconds = diff % 60;
    
    return `${minutes}m ${seconds}s`;
}

function getStatusBadge(status) {
    const badges = {
        'pending': '<span class="badge bg-warning">En attente</span>',
        'assigned': '<span class="badge bg-success">Assignée</span>',
        'in_progress': '<span class="badge bg-primary">En cours</span>',
        'completed': '<span class="badge bg-secondary">Terminée</span>',
    };
    return badges[status] || '<span class="badge bg-secondary">Inconnu</span>';
}

function formatDate(dateStr) {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleString('fr-FR');
}
