/**
 * Video Chat Client - Military-Grade Security
 *
 * Features:
 * - No hardcoded credentials
 * - CSRF protection for API calls
 * - Input validation
 * - Comprehensive error handling
 * - Secure session management
 */

// Get configuration from sessionStorage (populated by backend)
const APP_ID = sessionStorage.getItem('appId')
const TOKEN = sessionStorage.getItem('token')
const CHANNEL = sessionStorage.getItem('room')
let UID = sessionStorage.getItem('UID')
let NAME = sessionStorage.getItem('name')

// Validate required configuration
if (!APP_ID || !TOKEN || !CHANNEL || !UID || !NAME) {
    console.error('Missing required configuration. Redirecting to lobby...')
    window.location.href = '/'
}

// Initialize Agora client
const client = AgoraRTC.createClient({mode: 'rtc', codec: 'vp8'})

// State management
let localTracks = []
let remoteUsers = {}
let isJoined = false

// ==============================================================================
// CSRF TOKEN MANAGEMENT
// ==============================================================================

/**
 * Get CSRF token from cookie
 */
function getCookie(name) {
    let cookieValue = null
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';')
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim()
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1))
                break
            }
        }
    }
    return cookieValue
}

const csrftoken = getCookie('csrftoken')

// ==============================================================================
// MAIN FUNCTIONS
// ==============================================================================

/**
 * Join Agora channel and display local video stream
 */
let joinAndDisplayLocalStream = async () => {
    try {
        // Display room name
        document.getElementById('room-name').innerText = CHANNEL

        // Set up event handlers
        client.on('user-published', handleUserJoined)
        client.on('user-left', handleUserLeft)

        // Join channel with token
        try {
            UID = await client.join(APP_ID, CHANNEL, TOKEN, parseInt(UID))
            isJoined = true
            console.log('Successfully joined channel:', CHANNEL)
        } catch (error) {
            console.error('Failed to join channel:', error)
            alert('Failed to join video room. Please try again.')
            window.location.href = '/'
            return
        }

        // Get microphone and camera permissions
        try {
            localTracks = await AgoraRTC.createMicrophoneAndCameraTracks()
        } catch (error) {
            console.error('Failed to get media devices:', error)
            alert('Please allow microphone and camera access to join the video chat.')
            await client.leave()
            window.location.href = '/'
            return
        }

        // Create member in database
        let member = await createMember()
        if (!member) {
            console.error('Failed to create member record')
            await cleanup()
            return
        }

        // Create and display local video player
        let player = `<div class="video-container" id="user-container-${UID}">
                         <div class="video-player" id="user-${UID}"></div>
                         <div class="username-wrapper"><span class="user-name">${escapeHtml(member.name)}</span></div>
                      </div>`

        document.getElementById('video-streams').insertAdjacentHTML('beforeend', player)

        // Play local video
        localTracks[1].play(`user-${UID}`)

        // Publish local tracks to channel
        await client.publish([localTracks[0], localTracks[1]])
        console.log('Local stream published successfully')

    } catch (error) {
        console.error('Error in joinAndDisplayLocalStream:', error)
        alert('An error occurred while joining the room.')
        await cleanup()
    }
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    }
    return String(text).replace(/[&<>"']/g, m => map[m])
}

/**
 * Handle remote user joining and publishing media
 */
let handleUserJoined = async (user, mediaType) => {
    try {
        remoteUsers[user.uid] = user
        await client.subscribe(user, mediaType)

        if (mediaType === 'video') {
            let player = document.getElementById(`user-container-${user.uid}`)
            if (player != null) {
                player.remove()
            }

            // Get member info from backend
            let member = await getMember(user)
            if (!member) {
                console.error('Failed to get member info for UID:', user.uid)
                return
            }

            player = `<div class="video-container" id="user-container-${user.uid}">
                <div class="video-player" id="user-${user.uid}"></div>
                <div class="username-wrapper"><span class="user-name">${escapeHtml(member.name)}</span></div>
            </div>`

            document.getElementById('video-streams').insertAdjacentHTML('beforeend', player)
            user.videoTrack.play(`user-${user.uid}`)
            console.log('Remote video displayed for user:', user.uid)
        }

        if (mediaType === 'audio') {
            user.audioTrack.play()
            console.log('Remote audio playing for user:', user.uid)
        }
    } catch (error) {
        console.error('Error handling user joined:', error)
    }
}

/**
 * Handle remote user leaving
 */
let handleUserLeft = async (user) => {
    try {
        delete remoteUsers[user.uid]
        let container = document.getElementById(`user-container-${user.uid}`)
        if (container) {
            container.remove()
            console.log('User left:', user.uid)
        }
    } catch (error) {
        console.error('Error handling user left:', error)
    }
}

/**
 * Leave channel and cleanup
 */
let leaveAndRemoveLocalStream = async () => {
    await cleanup()
    window.location.href = '/'
}

/**
 * Cleanup function for leaving channel
 */
async function cleanup() {
    try {
        // Stop and close local tracks
        for (let i = 0; i < localTracks.length; i++) {
            try {
                localTracks[i].stop()
                localTracks[i].close()
            } catch (error) {
                console.error('Error closing track:', error)
            }
        }

        // Leave channel if joined
        if (isJoined) {
            await client.leave()
            isJoined = false
            console.log('Left channel successfully')
        }

        // Delete member from database
        await deleteMember()

        // Clear session storage
        sessionStorage.removeItem('UID')
        sessionStorage.removeItem('token')
        sessionStorage.removeItem('room')
        sessionStorage.removeItem('name')
        sessionStorage.removeItem('appId')

    } catch (error) {
        console.error('Error during cleanup:', error)
    }
}

/**
 * Toggle camera on/off
 */
let toggleCamera = async (e) => {
    try {
        if (localTracks[1].muted) {
            await localTracks[1].setMuted(false)
            e.target.style.backgroundColor = '#fff'
            console.log('Camera enabled')
        } else {
            await localTracks[1].setMuted(true)
            e.target.style.backgroundColor = 'rgb(255, 80, 80, 1)'
            console.log('Camera disabled')
        }
    } catch (error) {
        console.error('Error toggling camera:', error)
    }
}

/**
 * Toggle microphone on/off
 */
let toggleMic = async (e) => {
    try {
        if (localTracks[0].muted) {
            await localTracks[0].setMuted(false)
            e.target.style.backgroundColor = '#fff'
            console.log('Microphone enabled')
        } else {
            await localTracks[0].setMuted(true)
            e.target.style.backgroundColor = 'rgb(255, 80, 80, 1)'
            console.log('Microphone disabled')
        }
    } catch (error) {
        console.error('Error toggling microphone:', error)
    }
}

// ==============================================================================
// API FUNCTIONS
// ==============================================================================

/**
 * Create member in database (with CSRF protection)
 */
let createMember = async () => {
    try {
        let response = await fetch('/create_member/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
            credentials: 'same-origin',
            body: JSON.stringify({
                'name': NAME,
                'room_name': CHANNEL,
                'UID': UID
            })
        })

        if (!response.ok) {
            let error = await response.json()
            throw new Error(error.error || 'Failed to create member')
        }

        let member = await response.json()
        console.log('Member created:', member)
        return member
    } catch (error) {
        console.error('Error creating member:', error)
        alert('Failed to join room: ' + error.message)
        return null
    }
}

/**
 * Get member info from database
 */
let getMember = async (user) => {
    try {
        let response = await fetch(
            `/get_member/?UID=${encodeURIComponent(user.uid)}&room_name=${encodeURIComponent(CHANNEL)}`,
            {
                credentials: 'same-origin'
            }
        )

        if (!response.ok) {
            let error = await response.json()
            throw new Error(error.error || 'Failed to get member')
        }

        let member = await response.json()
        return member
    } catch (error) {
        console.error('Error getting member:', error)
        return null
    }
}

/**
 * Delete member from database (with CSRF protection)
 */
let deleteMember = async () => {
    try {
        let response = await fetch('/delete_member/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
            credentials: 'same-origin',
            body: JSON.stringify({
                'name': NAME,
                'room_name': CHANNEL,
                'UID': UID
            })
        })

        if (!response.ok) {
            let error = await response.json()
            console.warn('Failed to delete member:', error.error)
        } else {
            console.log('Member deleted successfully')
        }
    } catch (error) {
        console.error('Error deleting member:', error)
    }
}

// ==============================================================================
// EVENT LISTENERS
// ==============================================================================

// Cleanup when user closes tab/window
window.addEventListener("beforeunload", deleteMember)

// Start the application
joinAndDisplayLocalStream()

// Set up control button listeners
document.getElementById('leave-btn').addEventListener('click', leaveAndRemoveLocalStream)
document.getElementById('camera-btn').addEventListener('click', toggleCamera)
document.getElementById('mic-btn').addEventListener('click', toggleMic)

